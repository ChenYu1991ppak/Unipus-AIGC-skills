# -*- coding: utf-8 -*-
"""常量表：任务类型、状态、语言代码等。

来源：``fe-utils-pc`` / ``fe-aigc-platform`` 的 webpack 产物（见 docs/call-chains.md）。
"""


class Operation:
    """任务操作码。提交任务时作为 ``operation`` 字段。

    文档翻译既可以直接调 ``translate/create``（推荐），
    也可以走通用的 ``task/submit`` 通道。
    """

    Audio = 22
    STTS = 24
    Export = 26
    AudioSplit = 27
    AudioCheck = 29
    Meeting = 30
    AudioSubtitle = 31
    TextTranslate = 33
    DocumentTranslate = 34
    CompositionReview = 35      # 智能评阅-作文（一题一评，wm 通道）
    KBQA = 102                  # 知识库问答


class SubType:
    """智能评阅的语义子类型，对应 ``subType`` 字段。"""

    CompositionReview = 93


class TaskStatus:
    """``task/queryTask`` 返回的状态码。"""

    Submitted = 1
    Processing = 2
    Succeeded = 3
    Failed = 4

    NAME = {1: "已提交", 2: "处理中", 3: "成功", 4: "失败"}


class Level:
    """作文评阅的学段。"""

    COLLEGE = 0
    SENIOR = 1
    JUNIOR = 2
    PRIMARY = 3

    NAME = {0: "大学", 1: "高中", 2: "初中", 3: "小学"}


class RecordType:
    """翻译记录类型（``translate/create`` 的 ``type`` 字段）。"""

    TEXT = 1
    DOC = 2
    AUDIO = 3
    VIDEO = 4


# ISO-639-2 语言代码。注意后端使用的是三字母码，不是 "en"/"zh"。
LANGUAGES = {
    "zh": "zho", "cn": "zho", "中文": "zho",
    "en": "eng", "英语": "eng",
    "ja": "jpn", "日语": "jpn",
    "ko": "kor", "韩语": "kor",
    "fr": "fra", "法语": "fra",
    "de": "deu", "德语": "deu",
    "ru": "rus", "俄语": "rus",
    "es": "spa", "西班牙语": "spa",
    "ar": "ara", "阿拉伯语": "ara",
}


def norm_lang(code: str) -> str:
    """把 en/zh 这类常见的两字母码归一化成后端要的 ISO-639-2 三字母码。"""
    if not code:
        raise ValueError("语言代码不能为空")
    c = str(code).strip().lower()
    return LANGUAGES.get(c, c)
