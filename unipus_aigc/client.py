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

        self.translate = TranslateAPI(self)
        self.review = ReviewAPI(self)
        self.kb = KnowledgeBaseAPI(self)

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
    def post(self, path, payload=None, *, raw=False):
        """POST 到业务 API。

        :param path: ``translate/create`` 这样的相对路径（也可写全 ``/api/aigc/...``）
        :param raw: True 时返回原始响应对象（用于下载二进制）
        """
        url = path if path.startswith("http") else f"{config.API_BASE}/api/aigc/{path.lstrip('/')}"
        resp = self.session.post(url, json=payload or {}, timeout=self.timeout)
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
        self._sid = sio.sid
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

    def query_task(self, task_id):
        """查任务。成功时 ``responseData`` 里是 JSON 字符串形式的业务结果。"""
        return self.get_value("task/queryTask", {"taskId": task_id})

    def wait_task(self, task_id, *, interval=3, timeout=300, parse=True):
        """轮询直到任务到达终态，返回结果。

        ``queryTask`` 的 status：1 已提交 / 2 处理中 / 3 成功 / 4 失败。
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.query_task(task_id)
            status = _pick(last, "status")
            if status == TaskStatus.Succeeded:
                data = _pick(last, "responseData")
                if parse and isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        pass
                return data if data is not None else last
            if status == TaskStatus.Failed:
                raise TaskFailed(f"任务失败：{_pick(last, 'msg') or ''}",
                                 path="task/queryTask", payload=last)
            time.sleep(interval)
        raise TaskTimeout(f"任务 {task_id} 在 {timeout}s 内未完成",
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
