# -*- coding: utf-8 -*-
"""Unipus AIGC 平台的 Python 客户端。

覆盖已跑通的 AIGC 应用：

* **文档/文本翻译** —— :class:`~unipus_aigc.translate.TranslateAPI`
* **智能评阅（作文）** —— :class:`~unipus_aigc.review.ReviewAPI`
* **翻译评阅（打分，op36）** —— :class:`~unipus_aigc.trans_review.TransReviewAPI`
* **口语评阅（打分 + 发音反馈，op90）** —— :class:`~unipus_aigc.oral_review.OralReviewAPI`
* **知识库问答（RAG）** —— :class:`~unipus_aigc.kb_qa.KnowledgeBaseAPI`
* **RAG v2（会话式、分块级溯源）** —— :class:`~unipus_aigc.rag_v2.RagV2API`
* **语音合成** —— :class:`~unipus_aigc.speech.SpeechAPI`
* **同步 operation（11/13/14/15/17）** —— :class:`~unipus_aigc.sync_ops.SyncAPI`

快速上手::

    from unipus_aigc import UnipusAIGC

    with UnipusAIGC() as cli:       # token: 环境变量 -> ./.env -> ~/.config/unipus-aigc/.env
        print(cli.translate.text("Hello", "en", "zh")["translation"])
        print(cli.review.format_report(cli.review.essay("I has a dream.")))
        print(cli.trans_review.format_report(cli.trans_review.review(
            "The quick brown fox.", "敏捷的棕色狐狸。", src_lang="en", tgt_lang="zh")))
        print(cli.kb.answer_text(cli.kb.temporary_qa(
            "文档讲了什么？", ["doc.txt"])))

.. note::
   ``trans_review`` 是**评阅打分**（只回一个 ``score``，没有译文）；
   要译文用 ``translate``。别把两者混起来。

调用链与逆向说明见 ``docs/call-chains.md``。
"""

from .client import SyncOutcome, UnipusAIGC
from .constants import (Level, Operation, RecordType, SubType, TaskStatus,
                        norm_lang, norm_lang_short)
from .errors import AigcError, MissingTokenError, StillRunning, TaskFailed, TaskTimeout
from .kb_qa import KnowledgeBaseAPI
from .oral_review import OralReviewAPI
from .rag_v2 import (APPROVE_NO, APPROVE_YES, DOC_STATUS, KB_SOURCES,
                     TERMINAL_DOC_STATUS, RagSession, RagV2API, doc_type_of)
from .review import ReviewAPI
from .speech import SpeechAPI
from .sync_ops import SyncAPI
from .trans_review import TransReviewAPI
from .translate import TranslateAPI

__version__ = "1.0.0"

__all__ = [
    "UnipusAIGC",
    "SyncOutcome",
    "TranslateAPI",
    "ReviewAPI",
    "TransReviewAPI",
    "OralReviewAPI",
    "KnowledgeBaseAPI",
    "RagV2API",
    "RagSession",
    "SpeechAPI",
    "SyncAPI",
    "doc_type_of",
    "KB_SOURCES",
    "DOC_STATUS",
    "TERMINAL_DOC_STATUS",
    "APPROVE_YES",
    "APPROVE_NO",
    "AigcError",
    "MissingTokenError",
    "StillRunning",
    "TaskFailed",
    "TaskTimeout",
    "Operation",
    "SubType",
    "TaskStatus",
    "Level",
    "RecordType",
    "norm_lang",
    "norm_lang_short",
]
