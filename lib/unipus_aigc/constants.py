# -*- coding: utf-8 -*-
"""常量表：任务类型、状态、语言代码等。

来源有两处，**不要混为一谈**：

* **接口文档**（Confluence `64754844 · AIGC异步任务`）—— ``Operation`、
  ``TaskStatus``、``SubType`` 的权威取值，见各常量上的 ``doc`` 注记。
* **前端 webpack 产物**（``fe-utils-pc`` / ``fe-aigc-platform``）——
  逆向客户端侧的另一套编号（``meta.type`` 21–27、102/14、40/1001、
  102/1500-1502）。**两套编号不是同一个体系**，映射表不在文档里。

因此凡是前端逆向得来的值（如 ``KBQA = 102``），保留原值不改名——
它们已在真实链路上跑通；文档里的同义项另起名字。
"""


class Operation:
    """任务操作码，提交任务时作为 ``operation`` 字段。

    取值全部来自接口文档的 operation 枚举表。文档枚举到 101 为止；
    标 ``[前端]`` 的是逆向得来、文档未载的值，保留是因为已跑通。
    """

    # ---- 1–8 文章（推文）创作，异步流式 ----
    OutlineH1 = 1
    OutlineH2 = 2
    Body = 3
    Continue = 4
    Expand = 5
    Optimize = 6                  # 必传 tone
    OptimizeAll = 7               # 必传 tone
    SuggestTitle = 8

    # ---- 9–21 语音 / 图像 / 问答 ----
    SpeechSynthesis = 9           # 语音合成，必传 subType（4 合成 / 38 听力音频）
    AiDraw = 10
    CourseStandardQA = 11         # 同步
    QuestionGen = 12
    QuestionAnswer = 13           # 同步
    PromptOptimize = 14           # 同步
    PromptTranslate = 15          # 同步，中文提示词 -> 英文
    KBQA_Doc = 16                 # [文档] "知识库问答-新" 的枚举值，见下
    KBView = 17                   # 同步
    DocQA = 18
    HdImage = 19
    WebsiteQA = 20
    ImageEdit = 21

    # ---- 22–44 音频 / 克隆 / 文档 / 评阅 ----
    #
    # 这几个名字是逆向期定下的，那时没有文档；文档的叫法不同但码值一致，
    # 所以左边保留旧名当别名，右边用文档名。
    AudioEdit = 22
    Audio = 22                    # 旧名（文档：音频编辑）
    TtsClone = 24
    STTS = 24                     # 旧名
    DocSummary = 25
    AudioSynthesis = 26
    Export = 26                   # 旧名
    AudioSegment = 27
    AudioSplit = 27               # 旧名
    AudioTextCheck = 29
    AudioCheck = 29               # 旧名
    MeetingVoice = 30
    Meeting = 30                  # 旧名
    AudioSubtitle = 31
    ReadingMaterial = 32
    TextTranslate = 33
    DocTranslate = 34
    DocumentTranslate = 34        # 旧名
    WritingReview = 35            # 写作评阅，必传 subType
    CompositionReview = 35        # 旧名（前端叫"作文评阅"）
    TranslationReview = 36        # 翻译评阅，字段与 35 完全不同
    QuestionSort = 37
    PictureBookImage = 38
    PictureBookText = 39
    RagAudioToDoc = 40
    RagVideoToDoc = 41
    RagDocSummary = 42
    AiModelTranslate = 43
    ImageMatting = 44

    # ---- 50–70 知识总结 / 模版文案 / 绘本（异步流式）----
    KnowledgeSummary = 50
    TemplateOutlineH1 = 51
    TemplateOutlineH2 = 52
    TemplateBody = 53
    TemplateContinue = 54
    TemplateExpand = 55
    TemplateOptimize = 56
    TemplateOptimizeAll = 57
    TenderOptimizeAll = 58
    TenderLocalRewrite = 59
    PbSysRoleAction = 60
    PbUserRole = 61
    PbUserRoleAction = 62
    PbSceneImage = 63
    PbMaterialImage = 64
    PbMatting = 65
    PbImageFusion = 66
    PbText = 67
    PbRoleBackground = 68
    AliyunNlsTts = 70             # 阿里 nls 语音合成

    # ---- 80–101 转换 / 数字人 / 视频 / RAG ----
    DocConvert = 80               # office<->pdf
    DigitalHuman = 81
    VideoGen = 82                 # 图/文生视频 & 去水印
    OralReview = 90               # 口语评阅
    QAnythingDocQA = 100
    AssistantDocQA = 101

    # ---- 同步交互的 operation：结果就在 submit 响应里，**没有轮询这一步** ----
    #
    # 依据是**实测**（2026-09-20），不是照抄文档：接口文档的 operation 枚举表里
    # 这五条被标成"同步交互"（其余是"异步交互"），逐个提交后也验证了
    # `task/submit` 的响应里 `value.status` 已经是 3、`value.responseData` 已经填好。
    # 拿 submit_task + wait_task 去跑它们会一路轮询到超时。
    #
    # ⚠️ 这个集合**只是给调用方参考用的**，不要拿它去写死分支
    # （"是 SYNC 就一定不轮询"）。真正的判据在
    # `UnipusAIGC.submit_sync` 里——它是**就地看返回的 status** 决定的，
    # 因为平台的行为可能变。
    #
    # ⚠️ 里面 13（智能答题）实测回 `status=9`（见 TaskStatus 的 warning），
    # 15（中文提示词翻译）实测返回空 `content`——两条都跑不出真实结果，
    # 收在 SYNC 里是因为它们**确实是同步交互**，不代表能用。
    SYNC = frozenset({CourseStandardQA, QuestionAnswer, PromptOptimize,
                      PromptTranslate, KBView})

    # ---- 文档未载、由前端逆向得到 ----
    KBQA = 102                    # [前端] 知识库问答，kb_qa.py 用它，已跑通
    KBQA_Legacy = 102             # 旧名，等同 KBQA
    #
    # 实测结论（2026-09 对照实验）：同一条链路、同一个 kbId，
    #   operation=102 -> 命中知识库内容，返回 answer/sourceName/sourceUrl
    #   operation=16  -> 固定罐头拒答（"抱歉，我无法回答该问题。"）并附带一段
    #                    与本库无关的 docSource
    # 文档枚举表里的 16 走的是另一套入参（qaListId/qaId，**没有 kbId**），
    # 目前不可用。所以这里是两个码值、一条可用通道，不是笔误。


class SubType:
    """``subType`` 字段。注意它挂在**不同 operation 下语义不同**。

    文档里的 subType 至少有三套含义，用哪套取决于 operation：

    * op 9 语音合成：``4`` 语音合成 / ``38`` 生成听力音频
    * op 35 写作评阅：``93`` 提交类型
    * op 81/82 数字人/视频：``14`` 数字人 / ``15`` 图生视频 / ``16`` 文生视频 / ``17`` 去水印
    * op 23 语音克隆：那是 ``type`` 字段（1 系统音色 / 2 个人音色），不是 subType
    """

    # op 9
    SpeechSynthesis = 4
    ListeningAudio = 38

    # op 35 —— 逆向期定的名字，文档口径为"提交类型-93"
    CompositionReview = 93
    WritingReview = 93

    # op 36 翻译评阅。文档子类型表口径 "70-翻译评阅"，
    # 但**实测它当前不参与计算**：同一份输入给 subType 70 和 93，分数一模一样
    # （都是 97.66）。照文档给 70，别"优化"掉——平台可能哪天开始校验。
    TranslationReview = 70

    # op 81/82
    DigitalHuman = 14
    ImageToVideo = 15
    TextToVideo = 16
    RemoveWatermark = 17


class TaskStatus:
    """``task/submit`` / ``queryTask`` / 长连接推送共用的 7 态状态码。

    文档口径（Confluence 64754844）：

    ===== ==========
    取值  含义
    ===== ==========
    1     提交成功
    2     执行中
    3     执行成功
    4     执行失败
    5     解析异常
    6     排队中
    7     已取消
    ===== ==========

    旧版只建了 1–4 四个态，是**残缺的**：5/6/7 会落到"未知状态"。
    6（排队中）尤其常见——并发高时任务会先排队。

    .. warning::
       文档的枚举**不是全集**。2026-09 实测 operation 13（智能答题）的
       ``queryTask`` 回的是 ``status=9``——既不在这 7 个值里，也不在
       :attr:`TERMINAL` / :attr:`FAILED` 里。语义未知，所以
       :attr:`Unknown` **故意不算终态**：宁可让 ``wait_task`` 轮询到超时并把
       真实 status 带进报错里，也不要把一个没读懂的状态当成"成功"或"失败"。
       见到 9 请补充实测记录，不要照着猜。
    """

    Submitted = 1
    Processing = 2
    Succeeded = 3
    Failed = 4
    ParseError = 5
    Queued = 6
    Cancelled = 7

    #: 文档枚举之外、实测出现过的状态。**不是终态**（见类文档）。
    Unknown = 9

    NAME = {
        1: "提交成功",
        2: "执行中",
        3: "执行成功",
        4: "执行失败",
        5: "解析异常",
        6: "排队中",
        7: "已取消",
    }

    #: 不会再变化的终态——轮询到这些就该停。
    TERMINAL = (Succeeded, Failed, ParseError, Cancelled)

    #: 明确是失败的终态。
    FAILED = (Failed, ParseError)


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
    """把 en/zh 这类常见的两字母码归一化成 ISO-639-2 三字母码。

    适用于**文本翻译**（``translate/create`` 的 ``type=1``）——实测
    ``eng``/``zho`` 与 ``en``/``zh``/``cn`` 都能成功。
    ``translate/lang/list`` 返回的 202 个语种也全是三字母码。

    ⚠️ **不要拿它去喂文档翻译**。文档翻译吃两字母码，
    用 :func:`norm_lang_short`。
    """
    if not code:
        raise ValueError("语言代码不能为空")
    c = str(code).strip().lower()
    return LANGUAGES.get(c, c)


# ---- 文档翻译用的是另一套（旧的）语种表：只吃两字母码 ----
#
# 实测对照（同一个文件、其它字段全同，只改语种码）：
#
#   doc  en  -> zh   OK          doc  eng -> cn   FAIL（文档翻译失败）
#   doc  en  -> cn   OK          doc  eng -> zho  FAIL（文档翻译失败）
#   doc  zh  -> en   OK          doc  zho -> eng  FAIL（文档翻译失败）
#   doc  cn  -> en   OK
#
# 失败是**瞬时的**：记录顶层 status 立刻变 4、msg="文档翻译失败"，
# flowResponses[].status 反而是 0。
#
# 结论：文档翻译（type=2）**任何一侧**用三字母码都会失败。
# 只有 en / zh 两个语种经过实测，其余按 ISO-639-1 推断、未验证。
SHORT_OF = {
    "zho": "zh", "eng": "en", "jpn": "ja", "kor": "ko", "fra": "fr",
    "deu": "de", "rus": "ru", "spa": "es", "ara": "ar",
}


def norm_lang_short(code: str) -> str:
    """归一化成**文档翻译**要的两字母码。

    ``cn`` 会被收敛成标准的 ``zh``（两者实测都能用）。
    没有对应短码的语种（如 ``ace``）原样返回——能不能成由平台决定，
    本表只覆盖 :data:`LANGUAGES` 里那几种常见语种。
    """
    if not code:
        raise ValueError("语言代码不能为空")
    c = str(code).strip().lower()
    if c == "cn":
        return "zh"
    three = LANGUAGES.get(c, c)          # 别名/短码 -> 三字母；已是三字母则原样
    return SHORT_OF.get(three, three)
