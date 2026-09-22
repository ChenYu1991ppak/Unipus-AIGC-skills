# -*- coding: utf-8 -*-
"""文档 / 文本翻译。

调用链（详见内部的接口记录）::

    文本:  translate/create(type=1) -> translate/detail 轮询
    文档:  upload_file -> translate/create(type=2) -> translate/detail 轮询

**语种码有两套，别用错**（实测结论，与旧文档相反）::

    文本翻译 type=1 ：三字母 / 两字母都能过（eng/zho、en/zh、en/cn 均 OK）
    文档翻译 type=2 ：**只吃两字母码**。任何一侧用三字母码，任务会立刻失败
                      （顶层 status=4、msg="文档翻译失败"）

    实测对照：doc en->zh OK、en->cn OK、zh->en OK、cn->en OK；
              doc eng->zho FAIL、eng->cn FAIL、zho->eng FAIL。

所以 :meth:`submit_document` 走 :func:`~unipus_aigc.constants.norm_lang_short`，
:meth:`text` 走 :func:`~unipus_aigc.constants.norm_lang`。
注意 ``translate/lang/list`` 返回的 202 个语种**全是三字母码**，那份列表不能
直接拿去喂文档翻译。

**完成与失败的判定**（详见 :meth:`wait`）::

    完成 = translation / translateUrl 非空      （顶层 status 成功时是 1，不能用来判完成）
    失败 = 顶层 status == 4，原因在 msg 里       （flowResponses[].status 不可靠，实测失败时是 0）
"""

import json
import time

from .constants import RecordType, norm_lang, norm_lang_short
from .errors import StillRunning, TaskFailed, TaskTimeout


class TranslateAPI:
    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 文本翻译
    # ------------------------------------------------------------------
    def text(self, content, from_lang="en", to_lang="zh", name=None,
             *, poll_interval=2, timeout=120):
        """翻译一段文本，返回 ``translate/detail`` 的 value。

        :param content: 待翻译文本
        :param name: 记录名，默认由时间戳生成
        :param timeout: 注意这里是秒，文本翻译通常几秒到几十秒

        ``detail.value.translation`` 是 ``[{"src": ..., "tgt": ...}, ...]``
        的 JSON 字符串。
        """
        rec = self._create(
            name=name or f"文本翻译-{time.strftime('%Y%m%d-%H%M%S')}",
            type_=RecordType.TEXT,
            from_lang=from_lang,
            to_lang=to_lang,
            text=content,
        )
        detail = self.wait(rec["id"], poll_interval=poll_interval, timeout=timeout)
        detail.setdefault("id", rec["id"])
        return detail

    # ------------------------------------------------------------------
    # 文档翻译
    # ------------------------------------------------------------------
    def document(self, path, from_lang="en", to_lang="zh", name=None,
                 *, poll_interval=5, timeout=900):
        """上传本地文档并翻译，返回 ``translate/detail`` 的 value。

        结果文件地址在 ``value.translateUrl``；也可以用
        :meth:`download` 落到本地。

        .. warning:: 这是个**阻塞到出结果**的调用，文档翻译实测要几分钟到十几分钟。
           在 Bash 工具里跑会撞上 600s 上限。请改用 :meth:`submit_document`
           + :meth:`poll` 两步。
        """
        rec = self.submit_document(path, from_lang, to_lang, name=name)
        detail = self.wait(rec["id"], poll_interval=poll_interval, timeout=timeout)
        detail.setdefault("id", rec["id"])
        return detail

    def submit_document(self, path, from_lang="en", to_lang="zh", name=None):
        """**只提交，不等结果**：上传文件 + ``translate/create``，秒级返回。

        :return: ``translate/create`` 的 value，含记录 ``id``

        这是长任务的正确入口。拿到 ``id`` 之后用 :meth:`poll` 反复查——
        状态存在平台侧（``translate/list`` 也能看到），中断了可以重入。
        """
        url = self._c.upload_path(path, folder="doc")
        rec_name = name or path.rsplit("/", 1)[-1]
        rec = self._create(
            name=rec_name,
            type_=RecordType.DOC,
            from_lang=from_lang,
            to_lang=to_lang,
            url=url,
            short_lang=True,          # 文档翻译只吃两字母码，见 norm_lang_short
        )
        # translate/create 的响应**不回显**这些字段，补上我们实际发出去的值，
        # 否则调用方打印出来全是 None。
        rec.setdefault("originalUrl", url)
        rec.setdefault("name", rec_name)
        rec.setdefault("languageFrom", norm_lang_short(from_lang))
        rec.setdefault("languageTo", norm_lang_short(to_lang))
        return rec

    def document_from_bytes(self, content, filename, from_lang="en", to_lang="zh",
                            name=None, **kw):
        url = self._c.upload_file(content, filename, folder="doc")
        rec = self._create(
            name=name or filename,
            type_=RecordType.DOC,
            from_lang=from_lang,
            to_lang=to_lang,
            url=url,
            short_lang=True,          # 文档翻译只吃两字母码
        )
        return self.wait(rec["id"], poll_interval=kw.pop("poll_interval", 5),
                         timeout=kw.pop("timeout", 900))

    # ------------------------------------------------------------------
    # 底层接口
    # ------------------------------------------------------------------
    def _create(self, name, type_, from_lang, to_lang, *, text=None, url=None,
                short_lang=False):
        """建翻译记录。

        :param short_lang: ``True`` 时语种用**两字母码**。文档翻译（``type=2``）
            必须这么传——三字母码会让任务**立刻**失败（``status=4``、
            ``msg="文档翻译失败"``）。文本翻译两套都吃，沿用三字母码。
            详见 :func:`~unipus_aigc.constants.norm_lang_short`。
        """
        norm = norm_lang_short if short_lang else norm_lang
        payload = {
            "name": name,
            "type": type_,
            "languageFrom": norm(from_lang),
            "languageTo": norm(to_lang),
            "socketId": self._c.socket_id,
        }
        if text is not None:
            payload["originalText"] = text
        if url is not None:
            payload["originalUrl"] = url
        value = self._c.get_value("translate/create", payload)
        if not isinstance(value, dict) or "id" not in value:
            raise ValueError(f"translate/create 未返回 id: {value}")
        return value

    def detail(self, record_id):
        return self._c.get_value("translate/detail", {"id": str(record_id)})

    def records(self, page=1, size=50, record_type=None, include_text=False):
        """历史记录列表。

        ⚠️ ``include_text`` **不是"带不带正文"，而是记录类型的开关**（2026-09-21 实测）：
        ``False``（默认）只回 ``type=2`` 文档翻译，``True`` 只回 ``type=1`` 文本翻译，
        两者互斥、``totalCount`` 也不同。名字有误导性，别按字面理解。

        ⚠️ 显式传的 ``record_type`` 会拼进 ``type`` 字段，但**服务端忽略它**——
        ``type=1`` / ``type=2`` / 不传三者结果完全一致。要按类型筛只能用 ``include_text``。
        保留这个参数只为"调用方传了就原样发出去"，不要指望它过滤。
        """
        payload = {"pageNum": page, "pageSize": size, "includeText": include_text}
        if record_type is not None:
            payload["type"] = record_type
        return self._c.get_value("translate/list", payload)

    def delete(self, *record_ids):
        """删除翻译记录（连带清理记录引用）。"""
        out = []
        for rid in record_ids:
            out.append(self._c.get_value("translate/delete", {"id": str(rid)}))
        return out if len(out) > 1 else out[0]

    def download(self, result, dest):
        """把 ``detail.value`` 里的译文文件下载到 dest。"""
        url = result.get("translateUrl") if isinstance(result, dict) else result
        if not url:
            raise ValueError("结果里没有 translateUrl")
        return self._c.download(url, dest)

    def translated_text(self, result):
        """从 ``detail.value`` 里提取纯译文（docx 等非文本类型返回 None）。"""
        raw = (result or {}).get("translation")
        if not raw:
            return None
        try:
            pairs = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return raw
        if isinstance(pairs, list):
            return "\n".join(str(p.get("tgt", "")) for p in pairs if isinstance(p, dict))
        return raw

    # ------------------------------------------------------------------
    # 轮询
    # ------------------------------------------------------------------
    @staticmethod
    def failed_reason(detail):
        """判断 ``translate/detail`` 是否已失败，失败则返回原因文本，否则 ``None``。

        **失败信号在顶层 ``status == 4``**，配套的 ``msg`` 是给的原因
        （实测拿到过 ``"文档翻译失败"``）。``flowResponses[].status`` 不可靠：
        实测一次失败里它是 ``0``。两个都查，以顶层为准。

        注意顶层 ``status`` 是三态 **`2` 进行中 / `1` 成功 / `4` 失败**
        （2026-09-21 实测订正；早先这里写"成功和进行中都是 `1`"是错的）。
        所以它**不能**用来判断"完成了没有"（完成要看 ``translation`` /
        ``translateUrl`` 是否非空），但**能**用来判断"失败了没有"。
        """
        if not isinstance(detail, dict):
            return None
        if detail.get("status") == 4:
            return detail.get("msg") or "翻译任务失败（status=4）"
        for flow in detail.get("flowResponses") or []:
            if isinstance(flow, dict) and flow.get("status") == 4:
                return flow.get("msg") or "翻译任务失败（flowResponses.status=4）"
        return None

    def wait(self, record_id, *, poll_interval=4, timeout=600):
        """轮询 ``translate/detail`` 直到结果出现。

        完成判定只看结果字段（``translation`` / ``translateUrl``）非空——
        顶层 ``status`` 是三态 ``2`` 进行中 / ``1`` 成功 / ``4`` 失败，拿 ``1`` 判"完成"
        看着像对的（``1`` 确实是成功），但**不能拿它当"还在跑"**；而拿结果字段判，
        无论平台怎么改编号都不会误判。``status == 4`` 是**失败**，
        必须识别出来，否则一个已经死掉的任务会被当成"仍在处理中"无限轮询。

        :raises TaskFailed: ``status == 4``（任务真失败，``msg`` 里有原因）
        :raises TaskTimeout: 超时仍未出结果
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.detail(record_id) or {}
            if last.get("translation") or last.get("translateUrl"):
                return last
            reason = self.failed_reason(last)
            if reason:
                raise TaskFailed(f"翻译任务 {record_id} 失败：{reason}",
                                 path="translate/detail", payload=last)
            time.sleep(poll_interval)
        raise TaskTimeout(f"翻译任务 {record_id} 在 {timeout}s 内未完成",
                          path="translate/detail", payload=last)

    def poll(self, record_id, *, poll_interval=4, timeout=60):
        """**短轮询**：查一会儿，没出结果就抛 :class:`StillRunning`。

        这是长任务的正确查询方式。``timeout`` 默认 60s，远小于 Bash 工具的
        600s 上限，所以一次调用一定返回。没完成不是错误——CLI 映射成退出码 3，
        调用方稍后拿同一个 id 再来一次即可。

        :raises StillRunning: 到时仍未出结果（任务大概还在平台上跑）
        :raises TaskFailed: 任务真失败
        """
        try:
            return self.wait(record_id, poll_interval=poll_interval, timeout=timeout)
        except TaskTimeout as e:
            raise StillRunning(
                f"翻译任务 {record_id} 仍在处理中，{timeout}s 内未出结果。"
                f"稍后用同一个 id 再 poll 一次。",
                path="translate/detail", payload=e.payload,
            ) from e
