# -*- coding: utf-8 -*-
"""Unipus AIGC 平台的 Python 客户端。

覆盖三个已跑通的 AIGC 应用：

* **文档/文本翻译** —— :class:`~unipus_aigc.client.TranslateAPI`
* **智能评阅（作文）** —— :class:`~unipus_aigc.client.ReviewAPI`
* **知识库问答（RAG）** —— :class:`~unipus_aigc.client.KnowledgeBaseAPI`

快速上手::

    from unipus_aigc import UnipusAIGC

    with UnipusAIGC() as cli:                      # token 从 .env / 环境变量读
        print(cli.translate.text("Hello", "en", "zh")["translation"])
        print(cli.review.format_report(cli.review.essay("I has a dream.")))
        print(cli.kb.answer_text(cli.kb.temporary_qa(
            "文档讲了什么？", ["doc.txt"])))

调用链与逆向说明见 ``docs/call-chains.md``。
"""

from .client import UnipusAIGC
from .constants import Level, Operation, RecordType, SubType, TaskStatus, norm_lang
from .errors import AigcError, TaskFailed, TaskTimeout
from .kb_qa import KnowledgeBaseAPI
from .review import ReviewAPI
from .translate import TranslateAPI

__version__ = "1.0.0"

__all__ = [
    "UnipusAIGC",
    "TranslateAPI",
    "ReviewAPI",
    "KnowledgeBaseAPI",
    "AigcError",
    "TaskFailed",
    "TaskTimeout",
    "Operation",
    "SubType",
    "TaskStatus",
    "Level",
    "RecordType",
    "norm_lang",
]
