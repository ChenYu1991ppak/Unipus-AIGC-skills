# -*- coding: utf-8 -*-
"""文档 / 文本翻译。

调用链（详见 docs/call-chains.md）::

    文本:  translate/create(type=1) -> translate/detail 轮询
    文档:  upload_file -> translate/create(type=2) -> translate/detail 轮询

``translate/create`` 里 ``languageFrom`` / ``languageTo`` 必须是 ISO-639-2
三字母码（eng / zho），传 "en"/"zh" 会报 12003 语种不存在。
"""

import json
import time

from .constants import RecordType, norm_lang
from .errors import TaskTimeout


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
        """
        url = self._c.upload_path(path, folder="doc")
        rec = self._create(
            name=name or path.rsplit("/", 1)[-1],
            type_=RecordType.DOC,
            from_lang=from_lang,
            to_lang=to_lang,
            url=url,
        )
        detail = self.wait(rec["id"], poll_interval=poll_interval, timeout=timeout)
        detail.setdefault("id", rec["id"])
        return detail

    def document_from_bytes(self, content, filename, from_lang="en", to_lang="zh",
                            name=None, **kw):
        url = self._c.upload_file(content, filename, folder="doc")
        rec = self._create(
            name=name or filename,
            type_=RecordType.DOC,
            from_lang=from_lang,
            to_lang=to_lang,
            url=url,
        )
        return self.wait(rec["id"], poll_interval=kw.pop("poll_interval", 5),
                         timeout=kw.pop("timeout", 900))

    # ------------------------------------------------------------------
    # 底层接口
    # ------------------------------------------------------------------
    def _create(self, name, type_, from_lang, to_lang, *, text=None, url=None):
        payload = {
            "name": name,
            "type": type_,
            "languageFrom": norm_lang(from_lang),
            "languageTo": norm_lang(to_lang),
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
        """历史记录列表。"""
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
    def wait(self, record_id, *, poll_interval=4, timeout=600):
        """轮询 ``translate/detail`` 直到结果出现。

        注意：记录上的 ``status`` 字段恒为 1，**不能**当任务状态用——
        第一轮就返回会拿到 ``translation=null`` 的半成品。真正的任务状态在
        ``flowResponses[].status`` 里（4 = 失败）。所以这里只等结果字段。
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.detail(record_id) or {}
            if last.get("translation") or last.get("translateUrl"):
                return last
            for flow in last.get("flowResponses") or []:
                if flow.get("status") == 4:
                    raise TaskTimeout(f"翻译任务 {record_id} 失败",
                                      path="translate/detail", payload=last)
            time.sleep(poll_interval)
        raise TaskTimeout(f"翻译任务 {record_id} 在 {timeout}s 内未完成",
                          path="translate/detail", payload=last)
