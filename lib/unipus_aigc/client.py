# -*- coding: utf-8 -*-
"""UnipusAIGC 统一客户端：鉴权 / 七牛上传 / socketId / 任务提交与轮询。

典型用法::

    from unipus_aigc import UnipusAIGC
    with UnipusAIGC() as cli:
        cli.translate.text("Hello world", "en", "zh")

调用链细节见 docs/call-chains.md。
"""

import io
import json
import mimetypes
import time
import uuid

import requests

from . import config
from .constants import TaskStatus
from .errors import AigcError, TaskFailed, TaskTimeout


class SyncOutcome:
    """:meth:`UnipusAIGC.submit_sync` 的返回。

    平台的**同步 operation**（11 / 13 / 14 / 15 / 17，见
    :attr:`~unipus_aigc.constants.Operation.SYNC`）把结果直接放在
    ``task/submit`` 的响应里——``status`` 已经是 ``3``、``responseData``
    已经填好，**没有轮询这一步**。但"同步"是平台的实现细节，不是契约，
    所以这个返回**三种情形都装得下**，由调用方按 ``done`` / ``status`` 分支：

    ==================  ========  ============================================
    情形                 ``done``  怎么读
    ==================  ========  ============================================
    当场出结果           ``True``  结果在 :attr:`result`
    还在跑（1/2/6）      ``False`` 拿 :attr:`task_id` 走 :meth:`wait_task`
    ``status=9``         ``False`` 语义未知（见 :class:`TaskStatus`），
                                   **既不是成功也不是失败**，看 :attr:`raw`
    ==================  ========  ============================================

    ``status=9`` 那一行是**实测**撞出来的（operation 13）：它的
    ``responseData`` 是空的，当成功会拿到 ``None``，当失败又会凭空报错。
    所以这一档单独留出来，并附一句 :attr:`note` 给 caller 转述。
    """

    __slots__ = ("operation", "status", "task_id", "result", "raw", "done",
                 "note")

    def __init__(self, operation, status, task_id, result, raw, done, note=None):
        self.operation = operation
        self.status = status
        self.task_id = task_id
        self.result = result
        self.raw = raw
        self.done = done
        self.note = note

    def __repr__(self):
        return (f"<SyncOutcome op={self.operation} status={self.status} "
                f"done={self.done} taskId={self.task_id}>")


class UnipusAIGC:
    """平台客户端。持有一个 requests.Session 和一个 socket.io 长连接。"""

    def __init__(self, token=None, open_id=None, timeout=60, verbose=False):
        self.token = token or config.load_token()
        self.open_id = open_id or config.load_open_id(self.token)
        self.timeout = timeout
        self.verbose = verbose

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "token": self.token,
                "source": config.SOURCE,
                "Content-Type": "application/json",
            }
        )

        self._sio = None
        self._sid = None
        self._messages = []          # 收到的 WS 推送，供排查用
        self._upload_meta = None     # uploadToken 的返回值缓存

        # 各业务模块延迟导入，避免循环依赖
        from .translate import TranslateAPI
        from .review import ReviewAPI
        from .kb_qa import KnowledgeBaseAPI
        from .speech import SpeechAPI
        from .oral_review import OralReviewAPI
        from .trans_review import TransReviewAPI
        from .rag_v2 import RagV2API
        from .sync_ops import SyncAPI
        from .question_gen import QuestionGenAPI
        from .image_gen import ImageGenAPI
        from .article import ArticleAPI

        self.translate = TranslateAPI(self)
        self.review = ReviewAPI(self)
        self.kb = KnowledgeBaseAPI(self)
        self.speech = SpeechAPI(self)
        self.oral = OralReviewAPI(self)
        self.trans_review = TransReviewAPI(self)
        self.rag_v2 = RagV2API(self)
        self.sync = SyncAPI(self)
        self.question_gen = QuestionGenAPI(self)
        self.image_gen = ImageGenAPI(self)
        self.article = ArticleAPI(self)
        # 老链路叫 cli.kb，v2 叫 cli.kb_v2 更好记；两个名字指向同一个对象。
        self.kb_v2 = self.rag_v2

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.disconnect_socket()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def post(self, path, payload=None, *, raw=False, headers=None):
        """POST 到业务 API。

        :param path: ``translate/create`` 这样的相对路径（也可写全 ``/api/aigc/...``）
        :param raw: True 时返回原始响应对象（用于下载二进制）
        :param headers: 额外请求头（**会覆盖**同名默认头）。给
            :class:`~unipus_aigc.ticket.TicketSession` 之类要换鉴权头的场景用。
        """
        url = path if path.startswith("http") else f"{config.API_BASE}/api/aigc/{path.lstrip('/')}"
        resp = self.session.post(url, json=payload or {}, timeout=self.timeout,
                                 headers=headers)
        if raw:
            resp.raise_for_status()
            return resp
        return self._unwrap(resp, path)

    def upload_raw(self, path, payload):
        """上传文件接口用：不设 Content-Type，交给 multipart 自己带 boundary。"""
        url = f"{config.API_BASE}/api/aigc/{path.lstrip('/')}"
        headers = {k: v for k, v in self.session.headers.items()
                   if k.lower() != "content-type"}
        resp = requests.post(url, data=payload, headers=headers, timeout=self.timeout)
        return self._unwrap(resp, path)

    @staticmethod
    def _unwrap(resp, path):
        if resp.status_code != 200:
            raise AigcError(f"HTTP {resp.status_code}", code=resp.status_code,
                            path=path, payload=resp.text[:400])
        try:
            body = resp.json()
        except ValueError:
            raise AigcError("响应不是 JSON", path=path, payload=resp.text[:400])
        if isinstance(body, dict) and body.get("success") is False:
            raise AigcError(body.get("msg", "接口返回失败"), code=body.get("code"),
                            path=path, payload=body)
        return body

    def get_value(self, path, payload=None):
        """POST 并直接返回 ``value``。"""
        return (self.post(path, payload) or {}).get("value")

    # ------------------------------------------------------------------
    # socket.io —— 任务提交接口强制要求一个有效的 sid
    # ------------------------------------------------------------------
    def connect_socket(self):
        """连接 ``wss://umcs.unipus.cn/umcs`` 的 ``/aigc`` 命名空间，返回 socketId。

        查询参数必须拼在 URL 里：python-socketio 的 connect() 不接受 query 关键字。

        **返回的不是 ``sio.sid``，而是 ``{appId}:{openId}:{devId}`` 的拼接串。**
        这是 2026-09-21 实测出来的：两种形态**都能过** ``task/submit`` 的
        ``socketId不能为空`` 校验（那条校验只看非空），**但只有拼接串能把
        ``msg_aigc`` 推送投递到这条连接上**。给裸 ``sio.sid`` 时推送是**静默丢失**的
        ——任务照样跑完、``queryTask`` 照样回 ``status=3``，一条推送都收不到、
        也不报任何错。所以"推送不可靠"这句老话，一部分是**投递地址给错了**。

        对 op102（知识库问答）尤其致命：它的答案**只走推送**、
        ``responseData`` 恒为 ``null``，见 :meth:`KnowledgeBaseAPI.ask`。

        A/B 对照与定性记在 docs/call-chains.md §0「``socketId`` 的形态」。
        """
        if self._sid:
            return self._sid
        try:
            import socketio
        except ImportError:
            raise AigcError("缺少依赖，请先 pip install python-socketio websocket-client")

        url = (f"{config.WS_URL}?appId={config.WS_APP_ID}"
               f"&openId={self.open_id}&devId={config.WS_DEV_ID}")
        sio = socketio.Client(logger=False, engineio_logger=False)
        sio.on(config.WS_EVENT, namespace=config.WS_NAMESPACE)(self._on_message)
        sio.connect(url, socketio_path=config.WS_PATH,
                    namespaces=[config.WS_NAMESPACE],
                    transports=["websocket"], wait_timeout=20)
        self._sio = sio
        # 交给接口的 socketId 是拼接串，不是 sio.sid（见 docstring）。
        # 连接本身用的 appId/openId/devId 不变——URL 上那三个参数才是握手身份。
        self._sid = f"{config.WS_APP_ID}:{self.open_id}:{config.WS_DEV_ID}"
        return self._sid

    def _on_message(self, data):
        self._messages.append(data)
        if self.verbose:
            print(f"[WS] {json.dumps(data, ensure_ascii=False)[:300]}")

    @property
    def socket_id(self):
        return self.connect_socket()

    def disconnect_socket(self):
        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass
        self._sio = None
        self._sid = None

    # ------------------------------------------------------------------
    # 七牛上传
    # ------------------------------------------------------------------
    def _upload_token(self):
        """取上传凭证。返回的 token 是凭证，只在上传步骤内部使用，不要打印。"""
        if self._upload_meta is None:
            self._upload_meta = self.get_value("common/uploadToken", {})
        return self._upload_meta

    def upload_file(self, content, filename, folder="doc"):
        """上传字节内容到七牛，返回可被业务接口引用的 URL。

        :param folder: 对象 key 前缀，doc / kb / audio 等
        """
        meta = self._upload_token()
        key = f"{meta['fileKey']}/{folder}/{uuid.uuid4().hex[:12]}{_ext(filename)}"
        boundary = "----unipus" + uuid.uuid4().hex

        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = io.BytesIO()

        def field(name, value):
            body.write(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode()
            )

        field("key", key)
        field("token", meta["token"])          # 凭证直接进表单，绝不写日志
        body.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
        )
        body.write(content)
        body.write(f"\r\n--{boundary}--\r\n".encode())

        resp = requests.post(
            config.QINIU_UPLOAD_HOST,
            data=body.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=self.timeout * 2,
        )
        if resp.status_code != 200:
            raise AigcError(f"七牛上传失败 HTTP {resp.status_code}", path="qiniu",
                            payload=resp.text[:400])
        up = resp.json()
        return meta["domain"] + up.get("key", key)

    def upload_path(self, path, folder="doc"):
        """上传本地文件，返回可引用的 URL。"""
        with open(path, "rb") as fh:
            return self.upload_file(fh.read(), path.rsplit("/", 1)[-1], folder=folder)

    # ------------------------------------------------------------------
    # 任务通道
    # ------------------------------------------------------------------
    def submit_task(self, operation, submit_data, socket_id=None):
        """走 ``task/submit`` 提交异步任务。

        :param operation: 操作码，见 :class:`~unipus_aigc.constants.Operation`
        :param submit_data: 业务参数（会被 JSON 序列化成一个字符串字段）
        :return: taskId
        """
        if isinstance(submit_data, (dict, list)):
            submit_data = json.dumps(submit_data, ensure_ascii=False)
        payload = {
            "socketId": socket_id or self.socket_id,
            "operation": operation,
            "submitData": submit_data,
        }
        value = self.get_value("task/submit", payload) or {}
        task_id = (value.get("taskId") or value.get("tid") or value.get("id")
                   if isinstance(value, dict) else value)
        if not task_id:
            raise AigcError("提交成功但没有拿到 taskId", path="task/submit", payload=value)
        return task_id

    def submit_sync(self, operation, submit_data, socket_id=None):
        """提交**同步** operation，直接返回业务结果（不轮询）。

        同步 operation（11 课标问答 / 13 智能答题 / 14 提示词优化 /
        15 中文提示词翻译 / 17 知识库查看，见
        :attr:`~unipus_aigc.constants.Operation.SYNC`）在 ``task/submit``
        的响应里就把 ``status=3`` 和 ``responseData`` 一起给了。拿
        :meth:`submit_task` + :meth:`wait_task` 去跑它们会**白等到超时**——
        ``queryTask`` 之后查不到那个 taskId。

        **判据是就地看响应**，不是照 operation 查表：返回里 ``status``
        已经是终态就地解析，还是 ``1``/``2``/``6``（在跑）就退回异步语义、
        把 taskId 交给调用方。平台的行为可能变，就地判断更稳。

        :return: :class:`SyncOutcome`——三种情形都装得下，见它的文档。
        """
        if isinstance(submit_data, (dict, list)):
            submit_data = json.dumps(submit_data, ensure_ascii=False)
        payload = {
            "socketId": socket_id or self.socket_id,
            "operation": operation,
            "submitData": submit_data,
        }
        value = self.get_value("task/submit", payload) or {}
        if not isinstance(value, dict):
            raise AigcError("提交同步任务返回了非预期的结构", path="task/submit",
                            payload=value)
        status = value.get("status")
        task_id = value.get("taskId") or value.get("tid") or value.get("id")

        # 就地判断：终态就地把结果解出来，不写死"同步 operation 一定不轮询"。
        if status in TaskStatus.TERMINAL:
            return SyncOutcome(operation, status, task_id,
                               self.parse_task_result(value), value, True)

        # status=9：文档 7 态枚举之外，实测出现过（op13）。responseData 是空的，
        # **既不能当成功也不能当失败**，原样交回给调用方。
        # 这一档要排在 taskId 校验**前面**——语义未知时连"没有 taskId"都
        # 说明不了什么，不能拿一个通用报错把它盖掉。
        if status is not None and status not in TaskStatus.NAME \
                and status not in TaskStatus.TERMINAL:
            note = (f"operation {operation} 提交后回 status={status}——"
                    f"**不在文档的 7 态枚举里**，语义未知；"
                    f"这不是同步 task/submit 的预期形态，responseData 也没填。"
                    f"既不当成功也不当失败，原始 value 已一并返回。")
            return SyncOutcome(operation, status, task_id, None, value, False,
                               note=note)

        if not task_id:
            raise AigcError("提交成功但没有拿到 taskId", path="task/submit",
                            payload=value)

        # 1/2/6：真的还在跑。别丢 taskId——调用方拿它去 wait_task。
        return SyncOutcome(operation, status, task_id, None, value, False)

    def query_task(self, task_id):
        """查任务。成功时 ``responseData`` 里是 JSON 字符串形式的业务结果。"""
        return self.get_value("task/queryTask", {"taskId": task_id})

    def resubmit_task(self, task_id, socket_id=None):
        """重跑一个**已失败**的任务（``task/resubmit``）。

        文档口径：*"当任务返回执行失败状态的时候，可以重新提交当前任务"*。
        注意 ``socketId`` 在这个接口是**必填**的（``submit`` 里反而是选填）。
        """
        payload = {"socketId": socket_id or self.socket_id, "taskId": task_id}
        value = self.get_value("task/resubmit", payload) or {}
        new_id = (value.get("taskId") or value.get("tid") or value.get("id")
                  if isinstance(value, dict) else value)
        return new_id or task_id

    def cancel_task(self, task_id, cancel_reason=None):
        """取消任务（``task/cancel``）。

        返回原始 ``value``——里面有 ``success`` / ``message`` / ``status``，
        取消**是否生效要看 ``success``**，不是看 HTTP 200。
        """
        payload = {"taskId": task_id}
        if cancel_reason:
            payload["cancelReason"] = cancel_reason
        return self.get_value("task/cancel", payload)

    @staticmethod
    def parse_task_result(raw, parse=True):
        """把 ``queryTask`` 的原始返回解成业务结果。

        :return: 成功 -> 解析后的 ``responseData``；**未完成 -> ``None``**
        :raises TaskFailed: 终态为失败（status 4 执行失败 / 5 解析异常 / 7 已取消）

        ``queryTask`` 的 status 有 **7 个**取值：
        ``1`` 提交成功 / ``2`` 执行中 / ``3`` 执行成功 / ``4`` 执行失败 /
        ``5`` 解析异常 / ``6`` 排队中 / ``7`` 已取消。

        返回 ``None`` 表示"还在跑"，调用方据此决定继续轮询还是退出码 3。
        **6（排队中）也算还在跑**——并发高时任务会先排队，别把它当失败。
        """
        status = _pick(raw, "status")
        if status == TaskStatus.Succeeded:
            data = _pick(raw, "responseData")
            if parse and isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    pass
            return data if data is not None else raw
        if status in TaskStatus.FAILED:
            label = TaskStatus.NAME.get(status, status)
            raise TaskFailed(f"任务失败（{label}）：{_pick(raw, 'msg') or ''}",
                             path="task/queryTask", payload=raw)
        if status == TaskStatus.Cancelled:
            raise TaskFailed("任务已取消", path="task/queryTask", payload=raw)
        return None

    def wait_task(self, task_id, *, interval=3, timeout=300, parse=True):
        """轮询直到任务到达终态，返回结果。

        超时抛 :class:`TaskTimeout`，报错里会带上最后一次看到的 ``status``——
        **文档枚举之外的状态（见 :class:`TaskStatus` 的 warning）只能靠这个看出来**，
        否则一个语义未知的状态会表现为"莫名其妙地一直转圈"。
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.query_task(task_id)
            result = self.parse_task_result(last, parse=parse)
            if result is not None:
                return result
            time.sleep(interval)
        raise TaskTimeout(_timeout_msg(task_id, timeout, last),
                          path="task/queryTask", payload=last)

    # ------------------------------------------------------------------
    # 便捷封装
    # ------------------------------------------------------------------
    def download(self, url, dest):
        """下载结果文件（译文 docx 等）。"""
        resp = self.session.get(url, timeout=self.timeout * 4)
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            fh.write(resp.content)
        return dest


def _ext(filename):
    i = filename.rfind(".")
    return filename[i:] if i > 0 else ""


def _timeout_msg(task_id, timeout, raw):
    """拼 ``TaskTimeout`` 的报错文本，把最后一次的 status 一起带出来。"""
    status = _pick(raw, "status")
    label = TaskStatus.NAME.get(status)
    if label:
        detail = f"最后状态 status={status}（{label}）"
    elif status is None:
        detail = "没查到任何状态"
    else:
        detail = f"最后状态 status={status}——**不在文档的 7 态枚举里**，语义未知"
    return f"任务 {task_id} 在 {timeout}s 内未完成：{detail}"


def _pick(obj, key):
    """从嵌套字典里按 key 取值（平台返回结构层级不完全一致）。"""
    if not isinstance(obj, dict):
        return None
    if key in obj:
        return obj[key]
    for v in obj.values():
        if isinstance(v, dict):
            got = _pick(v, key)
            if got is not None:
                return got
    return None
