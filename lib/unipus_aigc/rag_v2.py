# -*- coding: utf-8 -*-
"""RAG 框架 v2（``rag/kbp/v2/project/*``）。

接口文档：Confluence ``64763379 · RAG框架对外接口``。

它跟 :mod:`unipus_aigc.kb_qa` 那套老链路（``rag/kbp/project/*`` + op102）
**不是同一代接口**，别混用：

===========  ==========================  ==========================
            老链路 ``cli.kb``            v2 ``cli.rag_v2`` / ``cli.kb_v2``
===========  ==========================  ==========================
建库         ``project/add``             ``v2/project/add``
提问         ``task/submit``(op102)      ``v2/project/answer/sync``
溯源         单条 ``sourceUrl``          分块级 ``source_file_info``
会话         无                          有（``v2/project/session/*``）
问答历史     无                          ``qaList`` / ``qaDel``
认可 / 反馈  无                          ``answer/approve`` / ``user/feedback``
===========  ==========================  ==========================

调用链::

    建库:  rag/kbp/v2/project/add        {userId, projectName, source} -> 裸 kbId
    传文档: upload_file(folder="kb")
            -> rag/kbp/v2/project/docUpload {userId,kbId,docName,url,type}
            -> rag/kbp/v2/project/docList   {userId,projectId} 轮询到 "green"
    开会话: rag/kbp/v2/project/session/add {userId,kbId,sessionName} -> 裸 sessionId
    提问:  rag/kbp/v2/project/answer/sync {sourceId,userId,kbId,sessionId,question}
            -> {qaId, answer}   **同步，不轮询**

四个反复踩的坑，写在前面：

* **``userId`` 是 JWT 的 ``openId``**，见 :func:`~unipus_aigc.config.load_user_id`。
  文档示例里那个 ``140e609f…`` 是另一个人的，照抄只会拿到空列表。
* **``source`` 必传且决定可见性**——用哪个 ``source`` 建的库，就只能在同一
  ``source`` 的 ``project/list`` 里看见。看不到库时先怀疑这个，不是"库丢了"。
* **``answer/sync`` 是同步接口**，响应里 ``value`` 就直接是 ``{qaId, answer}``，
  套 ``submit_task`` + ``wait_task`` 会白等到超时。
* **``session/add`` 返回裸字符串**（``project/add`` 也是），不是 ``{"sessionId": ...}``。
  解包一律走 :func:`_bare`，两种形状都吃。
"""

import json
import time
from collections import OrderedDict

from . import config
from .errors import AigcError, StillRunning, TaskTimeout

# ----------------------------------------------------------------------
# 常量（实测结论钉在代码里，照 speech.py 的 SPEAKERS 那样）
# ----------------------------------------------------------------------

#: ``project/list`` 的 ``source`` 取值——**必传**，不传报
#: ``code=100 业务Id不能为空``。
#:
#: 关键语义：**一个 ``source`` 建的库只会出现在同 ``source`` 的 ``list`` 里。**
#: 想一次看全所有 source，得用老的聚合接口 ``rag/kbp/project/kb/list``。
KB_SOURCES = OrderedDict([
    ("2", "前端 / 历史建的库"),
    ("1715", "平台化（基础侧）——文档与实测的默认值"),
    ("1720", "职教（文档注释里点名的）"),
    ("default", "默认知识库"),
])

#: ``docList`` 每行的 ``status``。
#:
#: ⚠️ **``gray`` 和 ``purple`` 的 ``chunkSize`` 都是 ``-1``**（实测），
#: 所以判完成必须同时看 ``status == "green"`` **且** ``chunkSize > 0``——
#: 跟老链路同一个坑（README 坑 #7）。只看 status 会在解析中途问到空答案。
DOC_STATUS = OrderedDict([
    ("gray", "未开始"),
    ("purple", "解析中"),
    ("green", "解析完成"),
])

#: 文档解析的终态。只有 ``green`` 是终态——``purple`` 卡住时不要当成"完成"。
TERMINAL_DOC_STATUS = ("green",)

#: ``sourceId``：业务 id。文档口径"基础侧使用 1715 即可"。
SOURCE_PLATFORM = "1715"

#: ``answer/approve`` 的 ``approve`` 取值。
APPROVE_DEFAULT = 0
APPROVE_YES = 1
APPROVE_NO = 2

#: ``docUpload`` 的 ``type``：按扩展名推断。
_DOC_TYPES = {
    ".pdf": "pdf",
    ".doc": "word", ".docx": "word",
    ".xls": "excel", ".xlsx": "excel",
    ".ppt": "ppt", ".pptx": "ppt",
    ".txt": "document", ".md": "document",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio",
    ".png": "picture", ".jpg": "picture", ".jpeg": "picture",
    ".gif": "picture", ".bmp": "picture", ".webp": "picture",
}


def doc_type_of(filename):
    """按扩展名推断 ``docUpload`` 的 ``type``（小写扩展名）。"""
    i = str(filename).rfind(".")
    return _DOC_TYPES.get(str(filename)[i:].lower() if i > 0 else "", "document")


def _bare(value, *keys):
    """解包 ``value``：既可能是裸标量，也可能是 ``{key: scalar}``。

    ``project/add``、``session/add`` 返回的是**裸字符串**，
    但同一个接口在不同环境下偶尔会包一层。两种都吃，省得调用方判断。
    """
    if isinstance(value, dict):
        for k in keys:
            if value.get(k):
                return value[k]
        return None
    return value or None


def _base_name(filename):
    return str(filename).rsplit("/", 1)[-1]


class RagV2API:
    """RAG v2 接口面。挂 ``cli.rag_v2``（别名 ``cli.kb_v2``）。"""

    def __init__(self, client, source_id=SOURCE_PLATFORM):
        self._c = client
        self.source_id = str(source_id)

    # ------------------------------------------------------------------
    # 知识库
    # ------------------------------------------------------------------
    def create(self, name, source=SOURCE_PLATFORM):
        """建库，返回 **kbId 字符串**（``KB`` 开头的那个）。"""
        value = self._c.get_value("rag/kbp/v2/project/add", {
            "userId": self._user_id(),
            "projectName": name,
            "source": str(source),
        })
        kb_id = _bare(value, "kbId", "projectId", "id")
        if not kb_id:
            raise AigcError(f"建库未返回 kbId: {value!r}",
                            path="rag/kbp/v2/project/add", payload=value)
        return kb_id

    def rename(self, kb_id, new_name):
        """改名。返回 kbId。"""
        value = self._c.get_value("rag/kbp/v2/project/update", {
            "userId": self._user_id(),
            "projectId": kb_id,
            "projectName": new_name,
        })
        return _bare(value, "kbId", "projectId") or kb_id

    def delete(self, *kb_ids):
        """删库。参数名 ``projectIds``，是**数组**且支持批量。"""
        ids = [str(k) for k in kb_ids if k]
        if not ids:
            return None
        return self._c.get_value("rag/kbp/v2/project/del",
                                 {"userId": self._user_id(), "projectIds": ids})

    def projects(self, source=None):
        """列库。``source`` 必传，默认 :data:`SOURCE_PLATFORM`。

        :return: 库行列表——注意响应里键名是 **``infoList`` 不是 ``data``**。
        """
        value = self._c.get_value("rag/kbp/v2/project/list", {
            "userId": self._user_id(),
            "source": str(source if source is not None else self.source_id),
        })
        return (value or {}).get("infoList") or []

    def list(self, source=None):
        """同 :meth:`projects`，保留一个更短的名字给 CLI 用。"""
        return self.projects(source)

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------
    def upload_document(self, kb_id, path, doc_name=None, *, doc_type=None,
                        wait=False, poll_interval=5, timeout=300, **kw):
        """上传本地文件并挂进知识库。

        ``wait=False``（默认）时**只提交**，返回 ``{projectId, filename, url}``，
        之后用 :meth:`poll_document` 查解析状态。这是长任务的正确入口。
        """
        doc_name = doc_name or _base_name(path)
        with open(path, "rb") as fh:
            content = fh.read()
        url = self._c.upload_file(content, doc_name, folder="kb")
        return self.upload_by_url(kb_id, url, doc_name, doc_type=doc_type,
                                  size=len(content), wait=wait,
                                  poll_interval=poll_interval, timeout=timeout, **kw)

    def upload_by_url(self, kb_id, url, doc_name, *, doc_type=None, size=None,
                      wait=False, poll_interval=5, timeout=300, **kw):
        """把已在七牛的文档挂进知识库。

        ⚠️ ``docName`` **必须带后缀，且后缀必须小写**——``A.PDF`` 会被拒。
        """
        ext = doc_name[doc_name.rfind("."):] if "." in doc_name else ""
        if not ext:
            raise ValueError(
                f"docName {doc_name!r} 没有后缀。v2 的 docUpload 要求文件名带后缀。")
        if ext != ext.lower():
            raise ValueError(
                f"docName {doc_name!r} 的后缀 {ext!r} 是大写。v2 的 docUpload "
                f"要求后缀**必须小写**，{doc_name.rsplit('.', 1)[0]}.{ext.lower()} "
                f"这样的名字才能过。")
        body = {
            "userId": self._user_id(),
            "kbId": kb_id,
            "docName": doc_name,
            "url": url,
            "type": doc_type or doc_type_of(doc_name),
        }
        if size:
            body["bytes"] = int(size)
        body.update({k: v for k, v in kw.items() if v is not None})
        value = self._c.get_value("rag/kbp/v2/project/docUpload", body)
        info = value if isinstance(value, dict) else {}
        info.setdefault("projectId", kb_id)
        info.setdefault("filename", doc_name)
        info.setdefault("url", url)
        if not wait:
            return info
        return self.wait_document(kb_id, doc_name, poll_interval=poll_interval,
                                  timeout=timeout)

    def documents(self, kb_id, page=1, size=10):
        """列文档。

        ⚠️ **文档写的分页参数 ``pgeNum``（少一个 a）是错的——服务端不认它，
        只认 ``pageNum``。** 实测（3 篇文档、``pageSize=1``，列表按时间倒序）::

            pageNum=2            -> 第 2 篇（p2.txt）    ✅ 生效
            pgeNum=2             -> 第 1 篇（p3.txt）    ❌ 被忽略
            两个都发、值不同      -> 按 pageNum 走

        所以这里**只发 ``pageNum``**。曾经的写法是"两个都发、值相同"——那种
        写法碰巧能翻页（因为 pageNum 生效），但把 ``pgeNum`` 当成"另一个别名"
        记着，等于把文档的错拼法当成了真的；哪天服务端真去认 ``pgeNum`` 了，
        两个都发就会变成互相打架。既已实测，就按实测的写。
        """
        value = self._c.get_value("rag/kbp/v2/project/docList", {
            "userId": self._user_id(),
            "projectId": kb_id,
            "pageNum": page,
            "pageSize": size,
        })
        return value if isinstance(value, dict) else {}

    def doc_rows(self, kb_id, **kw):
        """只要文档行列表（``docList`` 的 ``docInfoList``）。"""
        return self.documents(kb_id, **kw).get("docInfoList") or []

    @staticmethod
    def is_ready(doc):
        """文档是否真的解析完成。

        **必须同时满足** ``status == "green"`` **且** ``chunkSize > 0``——
        ``gray`` / ``purple`` 的 ``chunkSize`` 都是 ``-1``，而 ``ready`` 字段
        在上传瞬间就已经是 ``"green"`` 了，只看它会问到空答案。

        老链路踩过同一个坑（README 坑 #7），v2 照旧。
        """
        if not doc:
            return False
        return (doc.get("status") in TERMINAL_DOC_STATUS
                and (doc.get("chunkSize") or 0) > 0)

    def find_document(self, kb_id, doc_name=None, **kw):
        rows = self.doc_rows(kb_id, **kw)
        if doc_name:
            rows = [d for d in rows if d.get("docName") == doc_name]
        return rows[-1] if rows else None

    def wait_document(self, kb_id, doc_name=None, *, poll_interval=5, timeout=300):
        """等文档解析完成，返回文档行。

        :raises StillRunning: 到时仍未完成（稍后用同一个 kbId 再来一次）
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.find_document(kb_id, doc_name)
            if self.is_ready(last):
                return last
            time.sleep(poll_interval)
        status = (last or {}).get("status")
        label = DOC_STATUS.get(status, "未出现在文档列表里")
        raise StillRunning(
            f"文档 {doc_name or '(最新一篇)'} 在 {timeout}s 内未解析完成："
            f"status={status}（{label}）、chunkSize={(last or {}).get('chunkSize')}。"
            f"稍后用同一个 kbId 再查一次。",
            path="rag/kbp/v2/project/docList", payload=last)

    def poll_document(self, kb_id, doc_name=None, *, poll_interval=5, timeout=60):
        """**短轮询**解析状态。语义同 :meth:`wait_document`，默认只等 60s。"""
        return self.wait_document(kb_id, doc_name, poll_interval=poll_interval,
                                  timeout=timeout)

    def delete_document(self, kb_id, file_id):
        """删文档。参数名是 ``fileId``（docList 行里的 ``docId``）。"""
        return self._c.get_value("rag/kbp/v2/project/deleteFile", {
            "userId": self._user_id(),
            "kbId": kb_id,
            "fileId": file_id,
        })

    def web_upload(self, url, project_id=None, name=None, source=None):
        """传一个 html 网址（**只支持无需登录的网站**）。

        :return: 知识库 id——``project_id`` 不给时平台会顺手把库建出来。
        """
        body = {"userId": self._user_id(), "url": url}
        if project_id:
            body["projectId"] = project_id
        if name:
            body["name"] = name
        if source is not None:
            body["source"] = str(source)
        value = self._c.get_value("rag/kbp/v2/project/webUpload", body)
        return _bare(value, "kbId", "projectId") or value

    # ------------------------------------------------------------------
    # 提示词（库级）
    # ------------------------------------------------------------------
    def set_prompt(self, kb_id, prompt):
        """设提示词。**已有就 update、没有就 add**——两个接口参数一样。"""
        path = ("rag/kbp/v2/project/update/prompt" if self.get_prompt(kb_id).get("prompt")
                else "rag/kbp/v2/project/add/prompt")
        value = self._c.get_value(path, {"kbId": kb_id, "prompt": prompt})
        return _bare(value, "kbId") or kb_id

    def get_prompt(self, kb_id):
        """取提示词。**不存在时返回 ``{"kbId": None, "prompt": None}``，不报错。**"""
        value = self._c.get_value("rag/kbp/v2/project/prompt", {"kbId": kb_id})
        return value if isinstance(value, dict) else {}

    def delete_prompt(self, kb_id):
        return self._c.get_value("rag/kbp/v2/project/del/prompt", {"kbId": kb_id})

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------
    def new_session(self, kb_id, name=None):
        """建会话，返回 **裸 sessionId 字符串**。"""
        value = self._c.get_value("rag/kbp/v2/project/session/add", {
            "userId": self._user_id(),
            "kbId": kb_id,
            "sessionName": name or time.strftime("会话-%Y%m%d-%H%M%S"),
        })
        sid = _bare(value, "sessionId", "id")
        if not sid:
            raise AigcError(f"建会话未返回 sessionId: {value!r}",
                            path="rag/kbp/v2/project/session/add", payload=value)
        return sid

    def rename_session(self, session_id, new_name):
        value = self._c.get_value("rag/kbp/v2/project/session/update", {
            "sessionId": session_id, "sessionName": new_name,
        })
        return _bare(value, "sessionId") or session_id

    def session_detail(self, session_id):
        """``sessionId`` / ``sessionName`` / ``kbId``。"""
        value = self._c.get_value("rag/kbp/v2/project/session/detail",
                                  {"sessionId": session_id})
        return value if isinstance(value, dict) else {}

    def sessions(self, kb_id):
        """某知识库下的会话列表——**最多最新的 100 条**，没有分页参数。"""
        value = self._c.get_value("rag/kbp/v2/project/session/list", {
            "userId": self._user_id(), "kbId": kb_id,
        })
        if isinstance(value, dict):
            return value.get("sessionList") or value.get("list") or []
        return value or []

    def delete_session(self, session_id):
        return self._c.get_value("rag/kbp/v2/project/session/delete",
                                 {"sessionId": session_id})

    def history(self, session_id):
        """问答记录（``qaList``）。

        ⚠️ 两处坑：

        * 每行的 ``role`` 是**中文**（``"用户"`` / ``"系统"``）。
        * 同一轮的**用户行和系统行共享一个 ``id``**——那个 ``id`` 是
          **问答对 id**，不是行主键。拿它当唯一键去重会丢掉一半的行。
        """
        value = self._c.get_value("rag/kbp/v2/project/session/qaList", {
            "userId": self._user_id(), "sessionId": session_id,
        })
        if isinstance(value, dict):
            return value.get("qaList") or value.get("list") or []
        return value or []

    def clear_history(self, session_id, qa_id=None):
        """清问答记录。

        ⚠️ ``qa_id`` **为空则删除整个会话下的问答记录**。
        这个默认行为很危险，CLI 侧的 dry-run 文案必须写清楚。
        """
        body = {"userId": self._user_id(), "sessionId": session_id}
        if qa_id is not None:
            body["id"] = qa_id
        return self._c.get_value("rag/kbp/v2/project/session/qaDel", body)

    def session(self, kb_id, name=None):
        """开一个会话，返回 :class:`RagSession`。"""
        return RagSession(self, kb_id, name=name)

    # ------------------------------------------------------------------
    # 问答
    # ------------------------------------------------------------------
    def ask(self, kb_id, question, session_id, *, histories=None, question_id=None):
        """**同步**问答，直接返回 ``{qaId, answer}``——**不要接轮询**。

        ``sessionId`` 是**必填**的（想不要会话用 :meth:`stream`）。
        ``histories`` 的元素是 ``{question, answer}``：两个字段都可以为空，
        但**不为空时必须成对出现**——只传 question 不传 answer 会被拒。
        """
        body = {
            "sourceId": str(self.source_id),
            "userId": self._user_id(),
            "kbId": kb_id,
            "sessionId": session_id,
            "question": question,
        }
        if histories:
            body["histories"] = _clean_histories(histories)
        value = self._c.get_value("rag/kbp/v2/project/answer/sync", body)
        result = value if isinstance(value, dict) else {"answer": value}
        if question_id is not None:
            result.setdefault("questionId", question_id)
        return result

    def approve(self, qa_id, approve=APPROVE_YES):
        """认可 / 不认可一条回答。

        ⚠️ 这里的入参叫 **``id``**，而 :meth:`feedback` 对同一个东西叫
        **``qaId``**——两个接口两个名字，文档如此。
        """
        return self._c.get_value("rag/kbp/v2/project/answer/approve", {
            "id": qa_id, "userId": self._user_id(), "approve": approve,
        })

    def feedback(self, qa_id, content):
        """给一条问答记录留反馈。入参名是 **``qaId``**（不是 ``id``）。"""
        return self._c.get_value("rag/kbp/v2/project/user/feedback", {
            "qaId": qa_id, "userId": self._user_id(), "content": content,
        })

    # ------------------------------------------------------------------
    # 流式（两条路径不一样，别搞混）
    # ------------------------------------------------------------------
    def stream(self, kb_id, question, *, histories=None, answer_prompt=None,
               model=None, timeout=None):
        """流式问答 —— ``rag/stream/answer``，**无需 sessionId**。

        文档标题就叫"AI口语-文档问答接口(流式)-无需sessionId"，路径里
        **没有** ``kbp/v2/``。这是跟 :meth:`answer_stream` 最要紧的区别。

        :return: 生成器，逐帧 yield 解析后的 dict
        """
        body = {
            "sourceId": int(self.source_id),
            "userId": self._user_id(),
            "kbId": kb_id,
            "question": question,
        }
        if histories:
            body["histories"] = _clean_histories(histories)
        if answer_prompt:
            body["answerPrompt"] = answer_prompt
        if model:
            body["model"] = model
        return self._iter_stream("rag/stream/answer", body, timeout=timeout)

    def answer_stream(self, kb_id, question, session_id, *, histories=None,
                      answer_prompt=None, model=None, timeout=None):
        """流式问答 —— ``rag/answer/stream``，**``sessionId`` 必填**。

        ⚠️ 这条路径**没有** ``kbp/v2/`` 前缀，而且**必须有 sessionId**。
        它和 :meth:`stream` 是两条不同的流式接口，别互相顶替。
        """
        body = {
            "sourceId": int(self.source_id),
            "userId": self._user_id(),
            "kbId": kb_id,
            "sessionId": session_id,
            "question": question,
        }
        if histories:
            body["histories"] = _clean_histories(histories)
        if answer_prompt:
            body["answerPrompt"] = answer_prompt
        if model:
            body["model"] = model
        return self._iter_stream("rag/answer/stream", body, timeout=timeout)

    def _iter_stream(self, path, body, *, timeout=None):
        """POST 一条 SSE 流，逐帧 yield :func:`parse_stream_frame` 的结果。"""
        url = f"{config.API_BASE}/api/aigc/{path}"
        resp = self._c.session.post(url, json=body, stream=True,
                                    timeout=timeout or self._c.timeout * 4)
        if resp.status_code != 200:
            raise AigcError(f"HTTP {resp.status_code}", code=resp.status_code,
                            path=path, payload=resp.text[:400])
        # ⚠️ 响应头是 `content-type: text/event-stream`，**不带 charset**。
        # requests 对 text/* 的默认编码是 ISO-8859-1（RFC 2616 的老规矩），
        # 而 `iter_lines(decode_unicode=True)` 又照抄 `resp.encoding`——
        # 不显式钉成 utf-8，中文会整段变成 `ä¸æ`。平台侧是 UTF-8。
        resp.encoding = "utf-8"
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                frame = parse_stream_frame(raw)
                if frame is not None:
                    yield frame
        finally:
            resp.close()

    def stream_text(self, kb_id, question, **kw):
        """流式问答，**只要最终文本**，拼接所有增量后返回。"""
        parts = []
        for frame in self.stream(kb_id, question, **kw):
            if frame.get("response"):
                parts.append(frame["response"])
        return "".join(parts)

    # ------------------------------------------------------------------
    # 平台预置问题
    # ------------------------------------------------------------------
    def basic_questions(self, num=3):
        """平台随机出的通用问题（``basic/ques/list``）。"""
        value = self._c.get_value("rag/kbp/v2/project/basic/ques/list", {
            "num": int(num), "userId": self._user_id(),
        })
        return value or []

    def mark_question_used(self, ques_id):
        """记一条"问题已使用"（``basic/ques/record``）。返回 ``None`` 是正常的。"""
        return self._c.get_value("rag/kbp/v2/project/basic/ques/record", {
            "userId": self._user_id(), "quesId": int(ques_id),
        })

    def kb_questions(self, kb_id, num=3):
        """某个知识库自己的预置问题（``basic/kb/ques/list``）。

        ⚠️ **这个接口文档载了、但实测打不通**——无论怎么传都回
        ``code=40010 元素个数不合法``。已排除的可能都列在下面，别再重跑一遍：

        ===========================================  ==========================
        探针                                          结果
        ===========================================  ==========================
        ``kbId``                                     ``40010 元素个数不合法``
        ``kb_id``                                    ``100 知识库Id不能为空``
        ``kbId`` + ``kb_id`` 都发                     ``40010``
        ``kbId`` + ``projectId`` / ``kbpId``          ``40010`` / ``100`` 缺 Id
        ``num`` 取 0/1/2/3/5/10                      全是 ``40010``
        ===========================================  ==========================

        两点可下的结论：

        * 服务端**读的是 ``kbId``**（只有发 ``kb_id`` 时报的是"知识库Id不能为空"），
          所以文档参数表里的 ``kb_id`` 是错的——文档入参示例才是对的。
        * 但传对了 key 也不通，报的"元素个数不合法"跟 id 和 ``num`` 都无关。

        所以**照文档发 ``kbId``**（跟示例一致），不学以前那样"两个都发"——
        赌一个拼法是赌，赌两个拼法也是赌，还多一个可能互相打架的字段。
        拿不到问题是平台侧的已知不通，不是调用方传错了；需要预置问题的话
        :meth:`basic_questions` 是通的。
        """
        value = self._c.get_value("rag/kbp/v2/project/basic/kb/ques/list", {
            "num": int(num),
            "userId": self._user_id(),
            "kbId": kb_id,
        })
        return value or []

    # ------------------------------------------------------------------
    def _user_id(self):
        """``userId`` —— 就是 JWT 的 ``openId``。

        单独走一个方法而不是直接读 ``self._c.open_id``，是为了让 v2 的代码
        读起来跟接口文档的字段名对得上，也方便将来平台换口径时改一处。
        """
        return config.load_user_id(self._c.token)


def _clean_histories(histories):
    """过滤 ``histories``。

    文档约束：每条 ``{question, answer}`` 里两个字段**可以都为空，但如果不为空
    必须成对出现**。所以单边的条目直接丢掉，不留半个给服务端去拒。
    """
    out = []
    for h in histories:
        q = (h or {}).get("question")
        a = (h or {}).get("answer")
        if q and a:
            out.append({"question": q, "answer": a})
    return out


def parse_stream_frame(raw):
    """把一行 SSE 数据解析成 ``{"response": ..., "source_file_info": ...}``。

    帧的 ``choices[0].delta.content`` 本身是一个 **JSON 字符串**，解析后里面
    可能还有 ``source_file_info``，而**它的值又是一个 JSON 字符串**——要
    ``loads`` 两次。这是这条接口最容易踩的一处。

    ⚠️ 另外两点实测结论（都跟"想当然"相反）：

    * ``source_file_info`` 解出来是 ``{"": {"file_id":…, "chunk_id":…,
      "content":…}}``——**外层那个键是空字符串**，真正的信息在唯一的值里。
      这里直接拍平成 ``[{file_id, chunk_id, content}, …]``，省得每个调用方
      都要写一次 `list(x.values())`。
    * **不需要 sessionId 也会有溯源**：无会话的 :meth:`RagV2API.stream` 一样
      吐 ``source_file_info``（帧顶层的 ``references`` / ``sourceFile`` 则
      **恒为 ``null``**，别去那儿找）。

    :return: ``{"type": ..., "response": ..., "source_file_info": [...]}``；
        不是数据行（如 ``[DONE]``）或解析不出来时返回 ``None``
    """
    line = raw.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line.startswith("[DONE]") or line.startswith(":"):
        return None
    try:
        frame = json.loads(line)
    except ValueError:
        return None
    choices = frame.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("delta") or {}).get("content")
    if not content:
        return None
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return {"type": None, "response": content, "source_file_info": None}
    if not isinstance(content, dict):
        return {"type": None, "response": content, "source_file_info": None}

    info = content.get("source_file_info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except ValueError:
            info = None
    if isinstance(info, dict):
        # 外层键是空串（见上），拍平成值列表；万一哪天键有名字也照拍。
        values = list(info.values())
        info = [v for v in values if isinstance(v, dict)] or None
    return {
        "type": content.get("type"),
        "response": content.get("response"),
        "source_file_info": info,
    }


class RagSession:
    """一个会话。把 ``sessionId`` 和问答历史收进对象里，省得调用方维护。

    ``histories`` 的元素约束（"question/answer 要么都空、要么都在"）很容易写错，
    所以 :meth:`ask` 每次成功后自己把 ``{question, answer}`` 追加进去::

        s = cli.rag_v2.session(kb_id, name="分析报告")
        s.ask("这篇讲了什么？")
        s.ask("那它的结论呢？")     # 自动带上上一轮
        s.history()
        s.close()

    调用方拿 ``s.last_qa_id`` 去做 ``approve`` / ``feedback``。
    """

    def __init__(self, api, kb_id, session_id=None, name=None):
        self._api = api
        self.kb_id = kb_id
        self.session_id = session_id or api.new_session(kb_id, name)
        self.session_name = name
        self.histories = []
        self.qa_ids = []
        self.last_qa_id = None

    def __repr__(self):
        return (f"RagSession(kb_id={self.kb_id!r}, "
                f"session_id={self.session_id!r}, turns={len(self.histories)})")

    def ask(self, question, *, use_history=True):
        """问一轮。返回原始 ``{qaId, answer}``。

        成功后自动把这一轮追加进 :attr:`histories`——**追问因此天然带上下文**。
        """
        result = self._api.ask(self.kb_id, question, self.session_id,
                               histories=self.histories if use_history else None)
        answer = (result or {}).get("answer")
        if answer:
            self.histories.append({"question": question, "answer": answer})
        qa_id = (result or {}).get("qaId")
        if qa_id is not None:
            self.qa_ids.append(qa_id)
            self.last_qa_id = qa_id
        return result

    def history(self):
        """平台侧的问答记录（``qaList``）。跟 :attr:`histories`（客户端攒的）不同。"""
        return self._api.history(self.session_id)

    def detail(self):
        return self._api.session_detail(self.session_id)

    def rename(self, new_name):
        self.session_name = new_name
        return self._api.rename_session(self.session_id, new_name)

    def approve(self, approve=APPROVE_YES, qa_id=None):
        return self._api.approve(qa_id or self.last_qa_id, approve)

    def feedback(self, content, qa_id=None):
        return self._api.feedback(qa_id or self.last_qa_id, content)

    def clear(self, qa_id=None):
        """清记录。``qa_id`` 不给 = **把这个会话的问答记录全清掉**。"""
        return self._api.clear_history(self.session_id, qa_id)

    def close(self):
        """删会话本身（问答记录跟着没）。**不可逆。**"""
        return self._api.delete_session(self.session_id)
