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
* **出题（阅读材料 → 出题 → 采纳）** —— :class:`~unipus_aigc.question_gen.QuestionGenAPI`
* **图像生成（AI 绘画 / 文生图）** —— :class:`~unipus_aigc.image_gen.ImageGenAPI`
* **任务生成** —— :mod:`unipus_aigc.exercise`。**任务由 agent 现写**，
  这个模块只负责查（`TASK_TYPES` / :func:`type_doc`）、收
  （:func:`add_task`）、验（:func:`check_task`）、交给 ``guide``
  （:func:`handoff_lines`）。**一条网络请求都不发。**

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
from .exercise import (ALL_TYPES, TASK_TYPES, add_task, catalog, check_task,
                       find_task, handoff_lines, load_set, new_set, render_task,
                       set_tasks, sets, type_doc)
from .sso import SsoError, encrypt_sso, login, refresh, needs_refresh
from .kb_qa import KnowledgeBaseAPI
from .article import (SUB_TYPES, TITLE_TYPES, TXT_TYPE, ArticleAPI, outline_markdown,
                      txt_struct)
from .image_gen import IMAGE_TYPES, KNOWN_GOOD_STYLE, ImageGenAPI, ImgStyle, images_of
from .oral_review import OralReviewAPI
from .question_gen import (EDUCATION, PLOY_CODES, RM_SUBTYPE, QuestionGenAPI,
                           questions_of)
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
    "QuestionGenAPI",
    "questions_of",
    "PLOY_CODES",
    "RM_SUBTYPE",
    "EDUCATION",
    "ImageGenAPI",
    "ImgStyle",
    "images_of",
    "IMAGE_TYPES",
    "KNOWN_GOOD_STYLE",
    "ArticleAPI",
    "txt_struct",
    "outline_markdown",
    "TITLE_TYPES",
    "TXT_TYPE",
    "SUB_TYPES",
    "doc_type_of",
    "KB_SOURCES",
    "DOC_STATUS",
    "TERMINAL_DOC_STATUS",
    "APPROVE_YES",
    "APPROVE_NO",
    "TASK_TYPES",
    "ALL_TYPES",
    "new_set",
    "add_task",
    "load_set",
    "set_tasks",
    "sets",
    "find_task",
    "check_task",
    "handoff_lines",
    "render_task",
    "catalog",
    "type_doc",
    "AigcError",
    "MissingTokenError",
    "SsoError",
    "encrypt_sso",
    "login",
    "refresh",
    "needs_refresh",
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
