# -*- coding: utf-8 -*-
"""知识库问答（RAG）。

调用链（详见 docs/call-chains.md）::

    建库:  rag/kbp/project/add       {projectName, desc, source}      -> kbId
    传文档: upload_file(folder="kb")
            -> rag/kbp/project/docUpload {kbId,docName,url,type:"document"}
            -> rag/kbp/project/docList   {projectId} 轮询到 status=="green"
    提问:  task/submit {operation:102, submitData:{question,kbId,source:1201}}
            -> task/queryTask 轮询到 status=3

字段名很容易踩坑：

* ``docList`` 参数是 ``projectId``（不是 kbId）
* ``project/add`` 参数是 ``projectName``（不是 kbName）
* ``docUpload`` 用 ``kbId`` + ``docName``（不是 projectId/docName 的其它拼法）
* 删除知识库是 ``project/del {projectIds: [kbId]}``，注意是数组
* 删除文档是 ``project/deleteFile {kbId, fileId}``
"""

import os
import time

from .constants import Operation
from .errors import TaskTimeout

# 文档解析完成的状态值
READY = "green"

SOURCE_WEB = 1201


class KnowledgeBaseAPI:
    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 知识库
    # ------------------------------------------------------------------
    def create(self, name, desc=""):
        """新建知识库，返回 kbId。"""
        value = self._c.get_value("rag/kbp/project/add", {
            "projectName": name,
            "desc": desc,
            "source": "2",
        })
        kb_id = (value or {}).get("kbId") if isinstance(value, dict) else value
        if not kb_id:
            raise ValueError(f"创建知识库未返回 kbId: {value}")
        return kb_id

    def list(self, page=1, size=50, name=None, source=None):
        payload = {"pageNum": page, "pageSize": size}
        if name:
            payload["name"] = name
        if source:
            payload["source"] = source
        return self._c.get_value("rag/kbp/project/kb/list", payload)

    def detail(self, kb_id):
        return self._c.get_value("rag/kbp/project/detail", {"projectId": kb_id})

    def delete(self, *kb_ids):
        """删除知识库。参数名是 ``projectIds`` 且必须是数组。"""
        return self._c.get_value("rag/kbp/project/del",
                                 {"projectIds": [str(k) for k in kb_ids]})

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------
    def upload_document(self, kb_id, path, doc_name=None, *, wait=True,
                        poll_interval=5, timeout=300):
        """上传本地文件到知识库并等解析完成（``status == "green"``）。

        :return: docList 里的文档信息 dict
        """
        doc_name = doc_name or os.path.basename(path)
        with open(path, "rb") as fh:
            url = self._c.upload_file(fh.read(), doc_name, folder="kb")
        return self.upload_document_by_url(kb_id, url, doc_name, wait=wait,
                                           poll_interval=poll_interval,
                                           timeout=timeout)

    def upload_document_by_url(self, kb_id, url, doc_name, *, wait=True,
                               poll_interval=5, timeout=300):
        self._c.get_value("rag/kbp/project/docUpload", {
            "kbId": kb_id,
            "docName": doc_name,
            "url": url,
            "type": "document",
        })
        if not wait:
            return None
        return self.wait_document(kb_id, doc_name, poll_interval=poll_interval,
                                  timeout=timeout)

    def documents(self, kb_id):
        """列文档。参数名是 ``projectId``。"""
        return self._c.get_value("rag/kbp/project/docList", {"projectId": kb_id})

    def wait_document(self, kb_id, doc_name=None, *, poll_interval=5, timeout=300):
        """等文档解析完成，返回文档信息 dict。

        判定条件是 ``status == "green"`` **且** ``chunkSize > 0``。
        刚上传时 ``ready`` 字段就已经是 "green" 而 ``status`` 还是 "gray"、
        ``chunkSize`` 是 -1，此时提问会拿到空答案——不要只看 ``ready``。
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            value = self.documents(kb_id) or {}
            docs = value.get("docInfoList") or []
            if doc_name:
                docs = [d for d in docs if d.get("docName") == doc_name] or docs
            last = docs[-1] if docs else None
            if last and last.get("status") == READY and (last.get("chunkSize") or 0) > 0:
                return last
            time.sleep(poll_interval)
        raise TaskTimeout(f"文档 {doc_name} 在 {timeout}s 内未解析完成",
                          path="rag/kbp/project/docList", payload=last)

    def delete_document(self, kb_id, file_id):
        """删文档。参数名是 ``kbId`` + ``fileId``（docList 里返回的 ``docId``）。"""
        return self._c.get_value("rag/kbp/project/deleteFile",
                                 {"kbId": kb_id, "fileId": file_id})

    # ------------------------------------------------------------------
    # 问答
    # ------------------------------------------------------------------
    def ask(self, kb_id, question, *, poll_interval=3, timeout=180):
        """对知识库提一个问题，返回结果 dict。

        结果里通常有 ``answer``（带 ``[1]`` 这样的引用角标）和溯源信息
        （``sourceUrl`` / ``sourceName`` 等）。
        """
        task_id = self._c.submit_task(Operation.KBQA, {
            "question": question,
            "kbId": kb_id,
            "source": SOURCE_WEB,
        })
        result = self._c.wait_task(task_id, interval=poll_interval, timeout=timeout)
        if isinstance(result, dict):
            result.setdefault("taskId", task_id)
        return result

    @staticmethod
    def answer_text(result):
        """从问答结果里提取纯文本答案。"""
        if not isinstance(result, dict):
            return str(result or "")
        for key in ("answer", "content", "text", "output"):
            if result.get(key):
                return result[key]
        return ""

    # ------------------------------------------------------------------
    # 高层封装：建库 -> 传文档 -> 提问 -> 清理
    # ------------------------------------------------------------------
    def temporary_qa(self, question, documents, name=None, cleanup=True,
                     **kw):
        """一次性问答：建临时知识库、传文档、提问，默认结束后删库。

        :param documents: 本地文件路径列表
        """
        kb_id = self.create(name or f"临时知识库-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            for path in documents:
                self.upload_document(kb_id, path, **kw)
            return self.ask(kb_id, question)
        finally:
            if cleanup:
                self.delete(kb_id)
