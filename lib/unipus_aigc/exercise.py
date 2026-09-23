# -*- coding: utf-8 -*-
"""任务生成的**工具层**：查、验、落盘、渲染。

.. note::
   **任务由 agent 现写，不由这个模块生成。** 早期版本在这里塞了一个素材库
   （20 篇短文、14 道作文题、10 个阅读选题…），用模板 + 槽位拼出任务来。
   那是错的：拼出来的是**同一道题的 N 个变体**，而人要的是"像真实任务"的
   内容——那只能现写。

   所以这个模块现在只做机器该做的事：

   * ``types`` / ``show <题型>``——**教 agent 一种题型长什么样**
     （题干怎么写、素材从哪来、执行交给谁、有哪些坑）
   * ``new`` / ``add``——**收下 agent 写好的任务**，一个任务一个文件
   * ``check``——验：结构齐不齐、应用名认不认识、shell 能不能解析、
     路径对不对得上、一个任务集里有没有重名
   * ``handoff`` / ``catalog`` / ``dump``——把任务交给 ``guide``

   **整个模块不 import client**，一行网络请求都不发。
"""

import glob
import json
import os
import re
import shlex
import time

from . import config
from .errors import AigcError


class LocalCheckError(AigcError):
    """本地校验没过——**一个请求都没发**。CLI 把它映射成退出码 2。

    任务不是合法 JSON、缺字段、应用名不认识、shell 解析不了，都属于这一档。
    它们跟"平台出错"必须分开，否则用户会以为平台坏了。
    """


#: 执行那一步允许出现的应用名（一一对应 ``skills/`` 下的应用 skill）。
KNOWN_APPS = (
    "translate", "review", "trans-review", "oral-review", "kb-qa",
    "speech", "question-gen", "image-gen", "text-gen",
)

#: 素材那一步的 ``app``：**不是平台应用**，人或者模型。
#:
#: ``student`` / ``teacher`` 只留给「**不落成文件**」的素材——
#: 题面、待译原文这类本来就写在 ``brief`` 里的东西；**不需要** ``shell`` /
#: ``saves_as`` / ``handoff``。
#:
#: 一旦写了 ``saves_as``（说明执行那一步会去读这个文件），就必须指明真正的
#: 产出方，不能再写 ``student``：文本归 :data:`AGENT_APP`，
#: 音频 / 图像归 ``speech`` / ``image-gen``。
HUMAN_APPS = ("student", "teacher")

#: **你（模型）自己写**的一个 ``app`` 取值——不是平台应用，也不是人。
#:
#: 平台上没有任何"产出一份文本文件"的接口（`speech` / `image-gen` 只产音频和
#: 图像），但任务要能**无人值守跑完**：阅读材料、学生作答、学生作文、学生译文
#: 都得先有个文件躺在那儿，后面那一步（出题 / 评阅）才有东西可吃。
#: 所以这些由模型**按素材自己的 ``output`` 现写**。
#:
#: ⚠️ **别读成"伪造学生作业"**：这是给老师用的练习包，学生交上来的那份是
#: **样例答案**（让评阅链有输入、让老师看见期望产出），不是某个真人的作答。
#: 所以 ``output`` 必须写实（词数、体裁、几个要点、什么不许出现），
#: 还要在 ``handoff`` 里说清"这写的是样例，评阅分不代表任何真实学生"。
AGENT_APP = "agent"

#: 按「**产出的东西是不是一个文件**」分档：
#:
#: * :data:`HUMAN_APPS` —— 不落文件，什么都不用写
#: * :data:`AGENT_APP` —— 模型现写文本文件，要 ``output`` / ``saves_as`` / ``handoff``
#: * :data:`KNOWN_APPS` —— 平台应用产出，要 ``shell`` / ``saves_as`` / ``handoff``
MATERIAL_KINDS = HUMAN_APPS + (AGENT_APP,) + KNOWN_APPS

#: ``materials[].app`` 允许出现的全部取值。
PRODUCERS = MATERIAL_KINDS


#: 已实测的 TTS 音色（``speech`` 的白名单）。**只是一份提示**——
#: 真要现问平台跑 `speech speakers`。写别的值 ``check`` 会**警告但不拦**，
#: 因为那个名单平台会变。
KNOWN_SPEAKERS = ("en_luka", "us_annie", "zh_ming", "zh_xiaoxiao", "zh_youyou")


#: ``materials[].who`` 该写什么——**纯给人看的**，校验碰都不碰它，
#: 真正决定"这一步能不能自动跑"的是 :data:`app`。
WHO_NAMES = {
    "student": "学生",
    "teacher": "教师",
    AGENT_APP: "模型（你自己写）",
}


def who_name(app):
    """``app`` → 中文说法。平台应用一律说成「平台（xxx）」。"""
    return WHO_NAMES.get(app, f"平台（{app}）")


# ======================================================================
# 题型说明：教 agent 一种题型长什么样
# ======================================================================
#
# ⚠️ 这里的东西**只给 agent 看**，不参与任何生成。没有素材库、没有模板。
#    `show <题型>` 打的就是它。

TASK_TYPES = {
    "oral-drill": {
        "summary": "英语朗读评测：学生朗读一段短文、录音提交，得到总分和逐词发音反馈",
        "application": "oral-review",
        "audience": "中学或大学英语课堂，一人一段，约 1 分钟",
        "materials": [
            ("朗读示范音频", "speech",
             "给学生一段标准范读。`speech say <短文> --speaker <音色> "
             "--language 2 [--speed 0.8|1.0|1.2] --out materials/NN-demo.mp3`"),
            ("朗读录音（合成）", "speech",
             "**平台没有「交一段录音」的入口**，所以这份「学生录音」由 `speech` 合成："
             "换一个跟示范音频**不同的音色**、语速稍慢，念**同一段原文**，"
             "`speech say <短文> --speaker <另一个音色> --language 2 --speed 0.8 "
             "--out materials/NN-recording.mp3`"),
        ],
        "shell": 'oral review materials/NN-recording.mp3 \\\n'
                 '    --content "<朗读原文，逐字同示范音频>" --ques-type 5',
        "outcome": "百分制总分 + 发音反馈（在 evaluation.feedback 那段文本里）；"
                   "quesType=5 另给 evaluation.sentences[]，逐句带分、句内嵌逐词分",
        "notes": [
            "**音色**：`en_luka`（英式）/ `us_annie`（美式），别的值先跑 "
            "`speech speakers` 现问。音色和 `--language 2` 要对得上。"
            "两份音频**用不同音色**，不然评出来的是同一段声音。",
            "**交去评阅的那份是 TTS 合成的，不是真人录音**——分数会偏高"
            "（短文本段实测 86–92）。这条任务的价值在「给学生一段范读」和「看反馈里"
            "该怎么读」，**不在测出学生多差**，写任务卡时要如实说。"
            "**得分档位主要反映选段长度**：文本越长、TTS 的句间停顿越格式化，"
            "分数反而越低——跨任务比这个分数没有意义。",
            "**题型只能填 1/3/5，而且 1 不是朗读短文**：1=单词测评（音频"
            "**不能超过 20 秒**）、3=句子测评（≤60 秒）、5=篇章测评。"
            "朗读素材都是 40～70 秒，所以只能走 3 或 5；默认给 5。",
            "`--content` 是必填的朗读原文，要和两份音频念的**逐字相同**。",
        ],
    },
    "essay-writing": {
        "summary": "英语写作：学生按题面写一篇作文，得到总分、分项和逐句纠错",
        "application": "review",
        "audience": "中学或大学英语课堂，课后写作，约 30 分钟",
        "materials": [
            ("作文题面", "teacher",
             "**写在任务卡里**（交际情境 + 体裁 + 词数 + 要点提示）——"
             "真实作文题本来就是这样给的"),
            ("学生作文", AGENT_APP,
             "**平台没法给一段文字打分以外的路子**，而评阅那一步要读到一个文件，"
             "所以**你按题面写一篇样例作文**存成 materials/NN-submission.txt。"
             "`output` 要写实：词数区间、体裁、必须覆盖哪几个要点、什么不许出现"
             "（比如不许超纲词）。**就照题面写一篇像学生的**，别写成范文。"),
        ],
        "shell": 'review essay --path materials/NN-submission.txt \\\n'
                 '    --topic "<话题>" --level <0 大学|1 高中|2 初中|3 小学>',
        "outcome": "加权总分 + 四个分项 + 逐句纠错（correct[]）",
        "notes": [
            "`--level` 走 `constants.Level` 那套：**0 大学 / 1 高中 / 2 初中 / 3 小学**。",
            "结果里 `content` / `language` / `organization` / `mechanics` 是"
            "**评语字符串**，不是分数；分数只有 `*Score` 后缀的字段。",
            "**交去评阅的是模型写的样例作文**——它代表「这个题面下一份合格的"
            "学生作业长什么样」，拿到的分是**题面和评分档位的体检**，"
            "不是任何真实学生的成绩。这一点要写进 `notes`。",
            "学生真写了作文，把 `materials/NN-submission.txt` 覆盖掉再跑同一条"
            "`shell` 即可——路径不用改。",
        ],
    },
    "reading-comprehension": {
        "summary": "阅读理解练习：先有阅读材料，据此出题，学生作答",
        "application": "question-gen",
        "audience": "中学或大学英语课堂，一节阅读课",
        "materials": [
            ("阅读材料", AGENT_APP,
             "**你写这篇短文**，存成 materials/NN-passage.txt——平台只有"
             "「建材料」的入口（`create-material`），它吃一份**已经写好的正文**。"
             "`output` 里写清词数、题材、生词控制（别超纲）。"),
            ("学生作答", AGENT_APP,
             "读完材料**按题作答、写成样例答案**存成 materials/NN-answers.txt。"
             "出题是异步的，作答要等 `questions generate` 出完题再写；"
             "`output` 里写清格式（每题一行「题号 + 答案」、选择题只写字母）。"),
        ],
        "shell": "questions create-material --path materials/NN-passage.txt "
                 "--education <1..7>   # → rmId\n"
                 "questions generate <rmId> --ploy <策略code>:<题数>",
        "outcome": "rmId + 一组带 quesId 的题目",
        "notes": [
            "**阅读材料删不掉**：平台没有 `rm/delete`，建一条少一条。"
            "所以已经写到文件里的材料用 `--material-file`，别在平台上重建。",
            "**答题没有平台接口**：文档里的 `ques/ans` 实测 404，平台不判分。"
            "所以材料本身的正文**用 `create-material --path` 交上去**就行，"
            "学生作答那份 `NN-answers.txt` 是写给老师人工批改用的样例，"
            "**不用**、也没法交给平台。",
            "`--education` 走 `question_gen.EDUCATION`：**1 小学…5 本科…7 其他，没有 0**。"
            "跟 `review` 的 `--level` **不是一套编号**。",
            "出题策略 `--ploy` 是 `code:count`，见 `question-gen` skill 的 `questions ploys`。"
            "**它跟 `create-material` 的 `subType` 不是一套编号**，别互相套用。",
            "`NN-answers.txt` **必须在 `questions generate` 出完题之后再写**——"
            "没看到题就写不出答案。写进 `notes` 提醒执行的人注意顺序。",
        ],
    },
    "translation-drill": {
        "summary": "翻译练习：学生按原文翻译，得到评分",
        "application": "trans-review",
        "audience": "中学或大学英语课堂，随堂练习",
        "materials": [
            ("待译原文", "teacher", "**写在任务卡里**，标明考点"),
            ("学生译文", AGENT_APP,
             "**你按原文翻一份样例译文**存成 materials/NN-submission.txt——"
             "评阅那一步要读到一个文件。`output` 里写清：对应哪个学段、"
             "要故意留一两处典型的初级错误（时态、冠词、中式语序），"
             "不然每份都是满分，评阅结果没信息量。"),
        ],
        "shell": 'trans-review review \\\n'
                 '    --src-text "<原文>" --tgt-file materials/NN-submission.txt \\\n'
                 '    --src-lang <en|zh> --tgt-lang <en|zh>',
        "outcome": "一个百分制分数",
        "notes": [
            "**翻译评阅只给分，不产出译文**——返回的 `translation` 是你提交的那份的"
            "原样存档，别读成「平台改过的」。",
            "语种码**只认小写 `en` / `zh`**，**写错不报错、只给假分数**"
            "（实测 `en`/`zho` → 42.58，`zh`/`en` → 0.00）。CLI 用 choices 挡住了。",
            "要评另一对语种**必须新建记录**，不要复用同一条 `--wm-id`。",
            "**评阅的这份译文是模型写的样例**，不是哪个学生的作业——"
            "分数反映的是原文难度和评分口径，写进 `notes`。",
            "学生真交了译文，覆盖 `materials/NN-submission.txt` 再跑同一条 `shell`。",
        ],
    },
    "picture-writing": {
        "summary": "看图作文：先出一张插图，学生看图写作，再交评阅",
        "application": "review",
        "audience": "中学英语写作课，一课时",
        "materials": [
            ("插图", "image-gen",
             '`image draw "<画面描述>" --style general_v2.1_L --size 正方形`'),
            ("学生作文正文", AGENT_APP,
             "**你看着那张插图的画面描述写一篇样例作文**存成 "
             "materials/NN-submission.txt——评阅那一步要读到一个文件。"
             "`output` 里写清词数区间、要覆盖到画面里的哪几样东西、"
             "什么不许出现（比如不许出现画面里没有的人物）。"),
        ],
        "shell": 'review essay --path materials/NN-submission.txt \\\n'
                 '    --topic "<题目>" --level <学段>',
        "outcome": "加权总分 + 四个分项 + 逐句纠错",
        "notes": [
            "**出图必须用 `general_v2.1_L`**：接口文档点名的那些风格"
            "（`manhua` / `shuicai` / `xieshi` 之类）虽然也在线上白名单里，"
            "但提交后会被**静默改写**、然后失败或挂住。尺寸也要跟着风格走。",
            "出图不是秒级，`--wait` 留足；超时是退出码 3，**不是失败**。",
            "**交去评阅的是模型照着插图写的样例作文**——分数代表「这个图"
            "加这个题面能激出什么样的学生文字」，不是任何真实学生的成绩。"
            "这一点要写进 `notes`。",
            "学生真写了作文，把 `materials/NN-submission.txt` 覆盖掉再跑同一条"
            "`shell` 即可——路径不用改。",
            "**插图必须先出、作文后写**：看不到画面就写不出贴题的作文。"
            "写进 `notes` 提醒执行的人注意顺序。",
        ],
    },
    "listening-comprehension": {
        "summary": "听力练习：先有听力脚本、合成音频，据此出题，学生作答",
        "application": "question-gen",
        "audience": "中学英语听力课，一节听说课",
        "materials": [
            ("听力脚本", AGENT_APP,
             "**你写这段脚本**，落成 materials/NN-script.txt——它既是下面合成音频"
             "那一步的输入，也是留给教师核对题目正误的依据。`output` 里写清词数、"
             "体裁（广播通知 / 独白 / 对话）。**不要提前发给学生**。"),
            ("听力音频", "speech",
             '`speech say "<脚本>" --speaker <音色> --language 2 '
             '[--speed 0.8|1.0] --out materials/NN-audio.mp3`'),
            ("学生作答", AGENT_APP,
             "听完**按题作答、写成样例答案**存成 materials/NN-answers.txt。"
             "出题是异步的，作答要等 `questions generate` 出完题再写；"
             "`output` 里写清格式（每题一行「题号 + 答案」、选择题只写字母）。"),
        ],
        "shell": "questions create-material --path materials/NN-script.txt "
                 "--education <1..7>   # → rmId\n"
                 "questions generate <rmId> --ploy <策略code>:<题数>",
        "outcome": "rmId + 一组带 quesId 的题目",
        "notes": [
            "**脚本要先写出来再合成**——`speech say` 直接吃文本，脚本是它的输入。"
            "脚本落成 `materials/NN-script.txt`，合成那一步从它读。",
            "出题那一步同样受「**阅读材料删不掉**」约束。要出题就得建材料。",
            "音色和语速影响难度：慢速（0.8）适合初中，原速适合高中以上。",
            "**答题没有平台接口**：文档里的 `ques/ans` 实测 404，平台不判分。"
            "所以 `NN-answers.txt` 是留给老师人工批改用的样例答案，"
            "**不用**、也没法交给平台。",
            "`NN-answers.txt` **必须在 `questions generate` 出完题之后再写**——"
            "没听到音、没看到题就写不出答案。写进 `notes` 提醒执行的人注意顺序。",
            "`--education` 走 `question_gen.EDUCATION`（**1 小学…5 本科…7 其他**），"
            "跟 `review` 的 `--level` **不是一套编号**。",
        ],
    },
}

ALL_TYPES = list(TASK_TYPES)


def type_doc(name):
    """``tasks show <题型>`` 打的东西——**教 agent 这种题型长什么样**。"""
    spec = TASK_TYPES.get(name)
    if spec is None:
        raise LocalCheckError(f"没有这种题型：{name!r}。可选："
                              f"{', '.join(ALL_TYPES)}")
    lines = [f"# 题型：{name}", "", spec["summary"], ""]
    lines.append(f"- **执行交给**：`{spec['application']}`")
    lines.append(f"- **适用**：{spec['audience']}")
    lines.append(f"- **产出**：{spec['outcome']}")
    lines.append("")
    lines.append("## 素材怎么来")
    lines.append("")
    for label, app, how in spec["materials"]:
        lines.append(f"- **{label}**（{who_name(app)}）：{how}")
    lines.append("")
    lines.append("## 执行命令")
    lines.append("")
    lines.append("```bash")
    lines.append(spec["shell"])
    lines.append("```")
    lines.append("")
    lines.append("> 路径里的 `NN-` 换成这条任务的编号（`01` / `02`…）。")
    lines.append("")
    lines.append("## 坑（写任务卡时要如实写进 `notes`）")
    lines.append("")
    for note in spec["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 一条任务的 JSON 形状")
    lines.append("")
    lines.append("照着 `tasks show <已有任务>` 抄，或看下面这个骨架：")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_skeleton(name, spec), ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def _skeleton(name, spec):
    materials = []
    for label, app, _how in spec["materials"]:
        item = {"label": label, "who": who_name(app), "app": app}
        if app not in HUMAN_APPS:
            item["saves_as"] = f"materials/NN-{len(materials) + 1}.…"
            item["handoff"] = "…"
        if app == AGENT_APP:
            # 模型现写的：**没有 shell**（不发平台请求），要点在 output 上——
            # 写清楚写成什么样，读的人（也是模型）才写得对。
            item["output"] = "<写成什么样：词数 / 体裁 / 覆盖哪些要点 / 不许出现什么>"
        elif app not in HUMAN_APPS:
            item["shell"] = "…"
        materials.append(item)
    return {
        "taskNo": "NN",
        "taskKey": name,
        "title": "<像真题一样的标题，比如「英语朗读评测：…」>",
        "application": spec["application"],
        "scenario": "<场景：口语练习 / 写作练习 / 阅读理解 …>",
        "goal": spec["summary"],
        "audience": spec["audience"],
        "level": 0,
        "levelName": "大学",
        "tags": ["…"],
        "brief": "<学生看到的那段话，含题面全文>",
        "materials": materials,
        "steps": [{"n": 1, "label": "<这一步做什么>",
                   "application": spec["application"],
                   "purpose": "<为什么要它>",
                   "command": "<CLI 子命令>",
                   "shell": spec["shell"],
                   "outcome": spec["outcome"],
                   "handoff": "<交给 guide 的那句话，口语化>"}],
        "notes": spec["notes"],
    }


# ======================================================================
# 任务集
# ======================================================================
#: 一个任务必须有的字段。缺一个就是 :class:`LocalCheckError`。
REQUIRED = ("taskNo", "title", "application", "brief", "materials", "steps")


def root_dir(out=None):
    """任务集的根目录。默认 ``~/.cache/unipus-aigc/tasks``。"""
    if out:
        return os.path.abspath(os.path.expanduser(out))
    base = config.cache_dir()
    if not base:
        raise AigcError("主目录不可用，无法确定落盘位置——请显式给 --out")
    return os.path.join(base, "tasks")


def new_set(title=None, out=None):
    """建一个空的任务集。返回 ``set_id``。**会写盘**（manifest + 空目录）。"""
    set_id = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root_dir(out), set_id)
    if os.path.exists(dest):
        set_id += "-%02d" % (len(glob.glob(dest + "*")) + 1)
        dest = os.path.join(root_dir(out), set_id)
    os.makedirs(os.path.join(dest, "tasks"), exist_ok=True)
    os.makedirs(os.path.join(dest, "materials"), exist_ok=True)
    manifest = {
        "setId": set_id,
        "title": title or f"任务集 {set_id}",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "applications": [],
        "count": 0,
        "tasks": [],
    }
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    return set_id


def sets(out=None):
    """已有的任务集（新→旧）。"""
    root = root_dir(out)
    if not os.path.isdir(root):
        return []
    return sorted((n for n in os.listdir(root)
                   if os.path.isfile(os.path.join(root, n, "manifest.json"))),
                  reverse=True)


def _resolve_set(set_id=None, out=None, *, newest=True):
    """定位任务集。不给就取**最新那套**（没有就报错，不悄悄新建）。"""
    if set_id:
        path = os.path.join(root_dir(out), set_id, "manifest.json")
        if not os.path.isfile(path):
            raise AigcError(f"找不到任务集 {set_id}（{path}）")
        return set_id
    names = sets(out)
    if not names:
        raise AigcError("还没有任何任务集——先跑 `tasks new` 建一个")
    return names[0] if newest else names[-1]


def load_set(set_id=None, *, out=None):
    set_id = _resolve_set(set_id, out)
    with open(os.path.join(root_dir(out), set_id, "manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def set_tasks(set_id, *, out=None):
    """一个任务集里的全部任务（按编号）。"""
    tdir = os.path.join(root_dir(out), set_id, "tasks")
    if not os.path.isdir(tdir):
        raise AigcError(f"任务集 {set_id} 里没有任务（{tdir}）")
    out_list = []
    for name in sorted(os.listdir(tdir)):
        if name.endswith(".json"):
            with open(os.path.join(tdir, name), encoding="utf-8") as fh:
                out_list.append(json.load(fh))
    return out_list


def next_no(set_id, *, out=None):
    """下一个可用的编号（两位）。"""
    used = [t.get("taskNo") for t in set_tasks(set_id, out=out)]
    n = 1
    while f"{n:02d}" in used:
        n += 1
    return f"{n:02d}"


def add_task(task, set_id=None, *, out=None, path=None):
    """收下一条任务。返回 ``(set_id, 落盘路径)``。

    任务要从**文件**来（``--file``）或从**标准输入**来（``--stdin``，推荐）——
    写临时文件很啰嗦。两条路都过同样的校验。
    """
    set_id = _resolve_set(set_id, out)
    dest_dir = os.path.join(root_dir(out), set_id, "tasks")
    os.makedirs(dest_dir, exist_ok=True)

    task = _normalize(task, set_id=set_id, out=out)

    slug = re.sub(r"[^a-zA-Z0-9_-]", "", task.get("taskKey") or "task") or "task"
    dest = os.path.join(dest_dir, f"{task['taskNo']}-{slug}.json")
    _write_json(dest, task)
    _refresh_manifest(set_id, out=out)
    if path:
        _write_task_md(os.path.join(dest_dir, f"{task['taskNo']}-{slug}.md"), task)
    return set_id, dest


def add_dir(src, set_id=None, *, out=None):
    """把一整个目录里的 ``*.json`` 都收进来（agent 一次写好几条时用）。

    **先全验一遍再一起写**——一条坏的不该让半批脏数据落盘，
    也不该把另外四条好的连坐。报错要**指名道姓**是哪个文件。
    """
    paths = sorted(glob.glob(os.path.join(os.path.expanduser(src), "*.json")))
    if not paths:
        raise LocalCheckError(f"{src} 里没有 *.json")

    parsed, bad = [], []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                parsed.append((path, json.load(fh)))
        except (OSError, ValueError) as e:
            bad.append(f"{os.path.basename(path)}：读不出来（{e}）")
    if bad:
        raise LocalCheckError("这一批没收下（**一条都没写**）：\n  - "
                              + "\n  - ".join(bad))

    # 用"假设已经加进去"的编号试一遍，编号冲突、字段缺失在这里就暴露
    set_id = _resolve_set(set_id, out)
    used = [t.get("taskNo") for t in set_tasks(set_id, out=out)] \
        if os.path.isdir(os.path.join(root_dir(out), set_id, "tasks")) else []
    staged = []
    existing = set_tasks(set_id, out=out) if os.path.isdir(
        os.path.join(root_dir(out), set_id, "tasks")) else []
    for path, task in parsed:
        try:
            probe = dict(task)
            if not probe.get("taskNo"):
                n = 1
                while f"{n:02d}" in used:
                    n += 1
                probe["taskNo"] = f"{n:02d}"
            used.append(str(probe["taskNo"]).zfill(2))
            _validate(probe, siblings=existing + [t for _, t in staged])
            staged.append((path, task))
        except LocalCheckError as e:
            bad.append(f"{os.path.basename(path)}：{e}")
    if bad:
        raise LocalCheckError("这一批没收下（**一条都没写**）：\n  - "
                              + "\n  - ".join(bad))

    out_paths = []
    for path, task in staged:
        _, dest = add_task(task, set_id, out=out, path=True)
        out_paths.append(dest)
    _refresh_manifest(set_id, out=out)
    return set_id, out_paths


def _validate(task, *, siblings=()):
    """**收下之前**的完整校验——收一条合规的，别让坏的落盘。

    跟 :func:`check_task` 是同一套判据（结构 + 应用名 + shell 能解析 +
    引用的文件前面出现过 + 同一套里标题不撞），只是**收的时候就拦**，
    而不是等 `tasks check` 事后发现。

    一个任务的所有问题一次全报出来——修一条只报一条太折磨人。
    """
    problems = []
    if not isinstance(task, dict):
        raise LocalCheckError("任务必须是一个 JSON 对象")

    missing = [k for k in REQUIRED if not task.get(k)]
    if missing:
        raise LocalCheckError(f"缺字段：{', '.join(missing)}")

    problems += check_task(task, siblings=siblings)

    # 编号形状（check_task 不管编号，那是收编时的事）
    no = task.get("taskNo")
    if no and not re.fullmatch(r"\d{1,2}", str(no)):
        problems.append(f"taskNo 必须是 1–2 位数字，收到 {no!r}")

    if problems:
        raise LocalCheckError("；".join(dict.fromkeys(problems)))
    return task


def _normalize(task, *, set_id, out):
    """补齐编号 + 校验。缺编号就分配一个，其余缺什么报什么。"""
    tasks = set_tasks(set_id, out=out) if os.path.isdir(
        os.path.join(root_dir(out), set_id, "tasks")) else []
    used = [t.get("taskNo") for t in tasks]
    if not task.get("taskNo"):
        n = 1
        while f"{n:02d}" in used:
            n += 1
        task["taskNo"] = f"{n:02d}"
    elif not re.fullmatch(r"\d{1,2}", str(task["taskNo"])):
        raise LocalCheckError(f"taskNo 必须是 1–2 位数字，收到 {task['taskNo']!r}")
    else:
        task["taskNo"] = str(task["taskNo"]).zfill(2)
        if task["taskNo"] in used:
            raise LocalCheckError(f"编号 {task['taskNo']} 在这个任务集里已经有了")

    _validate(task)

    task.setdefault("taskKey", task["application"])
    task.setdefault("scenario", "")
    task.setdefault("goal", "")
    task.setdefault("audience", "")
    task.setdefault("tags", [])
    task.setdefault("notes", [])
    task.setdefault("setId", set_id)

    # 缺 levelName 就从 level 补（`Level` 那套：0 大学 / 1 高中 / 2 初中 / 3 小学）
    if task.get("levelName") is None:
        from .constants import Level
        task["levelName"] = Level.NAME.get(task.get("level"), "")
    if task.get("level") is None:
        from .constants import Level
        task["level"] = Level.COLLEGE
    return task


def _refresh_manifest(set_id, *, out=None):
    tasks = set_tasks(set_id, out=out)
    path = os.path.join(root_dir(out), set_id, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        man = json.load(fh)
    man["count"] = len(tasks)
    man["applications"] = sorted({t["application"] for t in tasks})
    man["byTask"] = {}
    for t in tasks:
        man["byTask"][t.get("taskKey", "")] = \
            man["byTask"].get(t.get("taskKey", ""), 0) + 1
    man["tasks"] = [{"taskNo": t["taskNo"], "title": t["title"],
                     "taskKey": t.get("taskKey"), "application": t["application"]}
                    for t in tasks]
    _write_json(path, man)
    return man


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ======================================================================
# 校验
# ======================================================================
def check_task(task, *, siblings=()):
    """验一条任务。返回**问题列表**（空 = 通过）。不碰网络。"""
    problems = []
    for key in REQUIRED:
        if not task.get(key):
            problems.append(f"缺字段 `{key}`")
    if problems:
        return problems

    if task["application"] not in KNOWN_APPS:
        problems.append(f"application={task['application']!r} 不是已知应用")

    # 一个任务集里标题不该撞
    for other in siblings:
        if other is task:
            continue
        if other.get("title") == task.get("title"):
            problems.append(f"标题跟 {other.get('taskNo')} 撞了：{task['title']}")

    files = []
    for i, mat in enumerate(task.get("materials") or [], 1):
        tag = f"素材 {i}（{mat.get('label', '?')}）"
        app = mat.get("app")
        if app and app not in MATERIAL_KINDS:
            problems.append(f"{tag}的 app={app!r} 不认识")
        if mat.get("saves_as"):
            # **落成文件的就是要被执行那一步读的**——所以它必须有个真能产出它的
            # 东西。早先只收"平台产出的"，结果 oral-drill 那类任务全被判"引用了
            # 不存在的文件"（录音明明写着 saves_as）。
            files.append(mat["saves_as"])
            if app in HUMAN_APPS:
                problems.append(
                    f"{tag}写了 `saves_as`，`app` 却还是 {app!r}——"
                    f"写了 `saves_as` 就说明执行那一步要去读这个文件，"
                    f"它得有人真的产出：文本写 `{AGENT_APP}`，"
                    f"音频 / 图像写给对应的平台应用")
        if app == AGENT_APP:
            # **你现写的**：不发平台请求，所以**没有 shell**；
            # 要的是"写成什么样"和"存到哪"。
            if not mat.get("saves_as"):
                problems.append(f"{tag}是模型现写的，但没写 `saves_as`——"
                                f"执行那一步要知道文件在哪")
            if not mat.get("output"):
                problems.append(f"{tag}是模型现写的，但没写 `output`——"
                                f"不写清楚写成什么样，写出来就不是任务要的那份")
            if not mat.get("handoff"):
                problems.append(f"{tag}缺 `handoff`——交给 guide 时没话说")
        elif app and app not in HUMAN_APPS:
            if not mat.get("saves_as"):
                problems.append(f"{tag}交给 {app} 产出，但没写 `saves_as`")
            if not mat.get("handoff"):
                problems.append(f"{tag}缺 `handoff`——交给 guide 时没话说")
            if not mat.get("shell"):
                problems.append(f"{tag}缺 `shell`——光说「用哪个应用」不够，"
                                f"要写清楚命令怎么敲")
            problems += _shell_problems(mat.get("shell"), tag, files)
        if app == "speech" and (mat.get("args") or {}).get("speaker"):
            spk = mat["args"]["speaker"]
            if spk not in KNOWN_SPEAKERS:
                problems.append(f"{tag}的音色 {spk!r} 不在已实测的名单里"
                                f"（{'、'.join(KNOWN_SPEAKERS)}）——"
                                f"先跑 `speech speakers` 现问平台，改完再来")

    for i, step in enumerate(task.get("steps") or [], 1):
        tag = f"步骤 {i}（{step.get('label', '?')}）"
        if step.get("application") and step["application"] not in KNOWN_APPS:
            problems.append(f"{tag}的 application={step['application']!r} 不认识")
        if not step.get("shell"):
            problems.append(f"{tag}缺 `shell`")
        if not step.get("handoff"):
            problems.append(f"{tag}缺 `handoff`")
        problems += _shell_problems(step.get("shell"), tag, files)

    return problems


def _shell_problems(shell, tag, known_files):
    """验一个 shell：能解析吗？引用的文件前面出现过吗？有没有占位符残留？"""
    out = []
    if not shell:
        return out
    if "{{" in shell or "}}" in shell:
        out.append(f"{tag}的 shell 里还有没替换的 {{{{…}}}}")
    try:
        parts = shlex.split(shell.replace("\\\n", " "), comments=True)
    except ValueError as e:
        out.append(f"{tag}的 shell 解析不了（引号没配平？）：{e}")
        return out

    # 引用的 materials/... 必须在前面某一步产出过
    for tok in parts:
        for f in re.findall(r"materials/[A-Za-z0-9_.\-]+", tok):
            if known_files and not any(f == k or k.endswith(f) or f.endswith(k)
                                       for k in known_files):
                out.append(f"{tag}引用了 `{f}`，但前面没有任何素材产出这个文件")
    return out


def catalog(set_id=None, *, out=None):
    """任务集的目录（markdown）。"""
    set_id = _resolve_set(set_id, out)
    man = load_set(set_id, out=out)
    tasks = set_tasks(set_id, out=out)
    lines = [f"# {man.get('title') or set_id}", ""]
    if not tasks:
        lines += [f"（还没有任务——用 `tasks new` 建集，再用 `tasks add` 往里加。）", ""]
        return "\n".join(lines)
    lines.append(f"共 **{len(tasks)}** 条任务，覆盖 "
                 f"**{len(man['applications'])}** 个应用："
                 f"{'、'.join(man['applications'])}。")
    lines.append("")
    lines.append("> 这份清单由 `task-gen` 收编，**没有调用任何平台接口**。"
                 "每条任务都写明了交给哪个应用、素材从哪来。")
    lines.append("")
    lines.append("| # | 任务 | 交给哪个应用 | 场景 | 学段 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for t in tasks:
        lines.append(f"| {t['taskNo']} | {t['title']} | `{t['application']}` "
                     f"| {t.get('scenario', '')} | {t.get('levelName', '')} |")
    lines.append("")
    by_scene = {}
    for t in tasks:
        by_scene.setdefault(t.get("scenario") or "其它", []).append(t)
    lines.append("## 按场景分组")
    lines.append("")
    for scene, group in by_scene.items():
        lines.append(f"### {scene}（{len(group)} 条）")
        lines.append("")
        for t in group:
            lines.append(f"- **{t['taskNo']}　{t['title']}** —— {t.get('goal', '')}")
        lines.append("")
    lines.append("## 怎么用")
    lines.append("")
    lines.append("1. 每条任务的卡片在 `tasks/`，素材取件目录是 `materials/`。")
    lines.append("2. `tasks handoff <编号>` 打出**交给 `guide` 的话**，"
                 "照着念或粘过去——它会路由到对应应用。")
    lines.append("3. 素材准备（合成示范音频、起草阅读材料）也是**任务**，"
                 "同样交给 `guide`。")
    lines.append("")
    return "\n".join(lines)


def dump(set_id=None, *, out=None):
    """把整个任务集打成一个 JSON（给 agent 或别的程序读）。"""
    set_id = _resolve_set(set_id, out)
    return {"setId": set_id, "manifest": load_set(set_id, out=out),
            "tasks": set_tasks(set_id, out=out)}


# ======================================================================
# 渲染
# ======================================================================
def render_task(task, *, student=False):
    """一条任务的卡片。``student=True`` 时隐去标了 ``student_hidden`` 的条目。"""
    lines = [f"# 任务 {task.get('taskNo')}　{task['title']}", ""]
    lines.append(f"- **交给哪个应用**：`{task['application']}`")
    if task.get("scenario"):
        lines.append(f"- **场景**：{task['scenario']}")
    if task.get("goal"):
        lines.append(f"- **目标**：{task['goal']}")
    if task.get("levelName"):
        lines.append(f"- **学段**：{task['levelName']}")
    if task.get("audience"):
        lines.append(f"- **适用**：{task['audience']}")
    if task.get("tags"):
        lines.append(f"- **标签**：{'、'.join(task['tags'])}")
    lines.append("")

    lines.append("## 题目")
    lines.append("")
    lines += task.get("brief", "").splitlines()
    lines.append("")

    mats = [m for m in task.get("materials") or []
            if not (student and m.get("student_hidden"))]
    if mats:
        lines.append("## 素材准备")
        lines.append("")
        lines.append("这条任务要用到下面的东西。**没有任何一步要人来做**——"
                     "平台能产的交给平台（看「谁来做」是哪个应用），"
                     "平台产不了的本就是模型现写的（看「产出」那条怎么写）。")
        lines.append("")
        lines.append("| # | 素材 | 谁来做 | 存到哪 |")
        lines.append("| --- | --- | --- | --- |")
        for i, mat in enumerate(mats, 1):
            lines.append(f"| {i} | {mat['label']} | {mat['who']} "
                         f"| `{mat.get('saves_as', '—')}` |")
        lines.append("")
        for i, mat in enumerate(mats, 1):
            lines.append(f"### 素材 {i}　{mat['label']}")
            lines.append("")
            lines.append(f"- 谁来做：{mat['who']}")
            if mat.get("purpose"):
                lines.append(f"- 用途：{mat['purpose']}")
            if mat.get("command"):
                lines.append(f"- 命令：`{mat['command']}`")
            if mat.get("extra"):
                lines.append(f"- {mat['extra']}")
            if mat.get("shell"):
                lines.append("")
                lines.append("```bash")
                lines.append(mat["shell"])
                lines.append("```")
                lines.append("")
            if mat.get("saves_as"):
                lines.append(f"- 存到：`{mat['saves_as']}`")
            if mat.get("output"):
                lines.append(f"- 产出：{mat['output']}")
            lines.append("")
            if mat.get("app") and mat["app"] not in HUMAN_APPS \
                    and mat.get("handoff"):
                lines.append("  交接给 `guide`：")
                lines.append("")
                lines.append(f"  > {mat['handoff']}")
                lines.append("")

    lines.append("## 交给 guide 执行")
    lines.append("")
    for step in task.get("steps") or []:
        if student and step.get("student_hidden"):
            continue
        lines.append(f"**{step['label']}** → `{step.get('application', '')}`"
                     + (f"　命令：`{step['command']}`" if step.get("command")
                        else ""))
        lines.append("")
        if step.get("purpose"):
            lines.append(f"- 用途：{step['purpose']}")
        if step.get("outcome"):
            lines.append(f"- 产出：{step['outcome']}")
        if step.get("shell"):
            lines.append("")
            lines.append("```bash")
            lines.append(step["shell"])
            lines.append("```")
            lines.append("")
        if step.get("handoff"):
            lines.append("把下面这句话给 `guide`（它会路由到对应应用）：")
            lines.append("")
            lines.append(f"> {step['handoff']}")
            lines.append("")

    if task.get("notes"):
        lines.append("## 注意")
        lines.append("")
        for note in task["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def _write_task_md(path, task):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_task(task))


def handoff_lines(task, *, set_id=None):
    """``tasks handoff`` 打的东西——**只有交给 guide 的话**。"""
    head = f"任务 {task.get('taskNo')}　{task['title']}"
    if set_id:
        head += f"（任务集 {set_id}）"
    lines = [head, "", "素材："]
    n = 0
    for mat in task.get("materials") or []:
        if mat.get("app") in HUMAN_APPS:
            continue
        n += 1
        lines.append(f"  {n}. {mat['label']}　（{mat['who']}）")
        if mat.get("shell"):
            lines.append(f"     命令：{mat['shell'].splitlines()[0]}")
        if mat.get("handoff"):
            lines.append(f"     > {mat['handoff']}")
    if not n:
        lines.append("  （这条任务的素材都是人工准备的，不用交给 guide）")
    lines.append("")
    lines.append("执行：")
    for step in task.get("steps") or []:
        lines.append(f"  {step['label']} → `{step.get('application', '')}`")
        if step.get("handoff"):
            lines.append(f"     > {step['handoff']}")
    return "\n".join(lines)


def tasks_table():
    """``tasks types`` 的总览（纯文本）。"""
    rows = []
    for name in ALL_TYPES:
        spec = TASK_TYPES[name]
        rows.append(f"{name}　{spec['summary']}")
        rows.append(f"  执行交给：`{spec['application']}`")
        mats = "、".join(f"{label}（{who_name(app)}）"
                         for label, app, _ in spec["materials"])
        rows.append(f"  素材：{mats}")
        rows.append("")
    return "\n".join(rows).rstrip()


def find_task(no_or_title, set_id=None, *, out=None, newest_only=False):
    """按**编号**（``03``）或标题片段找一条任务。"""
    if newest_only and not set_id:
        names = sets(out)[:1]
    elif set_id:
        names = [_resolve_set(set_id, out)]
    else:
        names = sets(out)
    if not names:
        raise AigcError(f"还没有任何任务集（{root_dir(out)}）——先跑 `tasks new`")

    hits = []
    for sid in names:
        tdir = os.path.join(root_dir(out), sid, "tasks")
        if not os.path.isdir(tdir):
            continue
        for name in sorted(os.listdir(tdir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(tdir, name), encoding="utf-8") as fh:
                task = json.load(fh)
            if no_or_title.isdigit():
                matched = task.get("taskNo") == no_or_title.zfill(2)
            else:
                matched = (no_or_title in (task.get("title") or "")
                           or no_or_title in (task.get("taskKey") or "")
                           or name[:-5].startswith(no_or_title))
            if matched:
                hits.append((sid, os.path.join(tdir, name), task))
    if not hits:
        raise AigcError(f"找不到任务 {no_or_title}"
                        f"（在 {', '.join(names)} 下扫过）")
    if len(hits) > 1:
        raise AigcError(f"{no_or_title} 匹配到 {len(hits)} 条任务——"
                        f"请说详细一点："
                        + "、".join(f"{s}/{t.get('taskNo')} {t.get('title')}"
                                   for s, _, t in hits[:5]))
    return hits[0]
