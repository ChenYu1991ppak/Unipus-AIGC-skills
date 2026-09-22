---
name: task-gen
description: >-
  Create teaching tasks for the Unipus AIGC platform as a task list — 英语朗读
  评测 / 英语写作 / 阅读理解练习 / 翻译练习 / 看图作文 / 听力练习. **The tasks
  themselves are written by the model**, not generated from a bank: each one is a
  real classroom exercise with its own passage, essay prompt or listening script,
  and each records which application executes it and where its materials come
  from. A task list covers many applications and can be handed to guide for
  execution. Use when the user asks for 生成任务 / 造任务 / 任务清单 / 出一批练习 /
  出一套题 / 组卷 / 教学任务 / 布置作业 / 阅读练习 / 写作练习 / 翻译练习 /
  朗读任务 / 听力练习 / exercise / task list. 生成教学任务清单：默认不调用平台。
---

# Unipus AIGC · 任务生成

**产出是一份任务清单**——每条任务都像真实课堂里的一次作业或练习。

> ⭐ **任务是现写的。** 没有素材库、没有模板——你（模型）按题型的要求**自己
> 写内容**：一篇短文、一道作文题、一段听力脚本。程序只负责收下、校验、
> 交给 `guide`。
>
> **任务名要像真题**（"英语朗读评测：A Letter to My Teacher"、
> "看图作文：清晨的图书馆"），**不能是"XXX 测试"**。

## 先问清楚两件事

1. **要哪些题型**。现有六种（`tasks types` 可以看）：

   | 题型 | 执行交给 | 素材由谁产出 |
   | --- | --- | --- |
   | `oral-drill` 朗读评测 | `oral-review` | 示范音频 ← `speech` |
   | `essay-writing` 写作 | `review` | （题面写在任务里） |
   | `reading-comprehension` 阅读理解 | `question-gen` | 阅读材料 ← 你写，或 `text-gen` 起草 |
   | `translation-drill` 翻译 | `trans-review` | （原文写在任务里） |
   | `picture-writing` 看图作文 | `review` | 插图 ← `image-gen` |
   | `listening-comprehension` 听力 | `question-gen` | 脚本 ← 你写；音频 ← `speech` |

2. **要几条、什么学段**。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" tasks <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错（**一个请求都没发**）/ `3` 仍在处理中。

## 出题：四步

### ① 看题型要求

```bash
bash "$S/scripts/run.sh" tasks types              # 六种题型一览
bash "$S/scripts/run.sh" tasks show oral-drill     # 一种题型的详细要求
```

`tasks show <题型>` 会告诉你：**题干怎么写、素材从哪来、执行命令长什么样、
有哪些坑必须如实写进 `notes`**。**写之前先看这个**——它替你省掉一次试错。

### ② 建一个任务集

```bash
bash "$S/scripts/run.sh" tasks new --title "高二英语·九月练习"
```

回一个任务集 id（形如 `20260922-154727`）。

### ③ 把写好的任务交进去

**推荐走 `--stdin`**（省得写临时文件）：

```bash
bash "$S/scripts/run.sh" tasks add --stdin <<'JSON'
{
  "taskKey": "oral-drill",
  "title": "英语朗读评测：A Letter to My Teacher",
  "application": "oral-review",
  "scenario": "口语练习",
  "level": 1,
  "goal": "学生朗读一封感谢信，录音提交，得到总分和逐词发音反馈",
  "audience": "高二英语课堂，一人一段，约 1 分钟",
  "tags": ["口语", "朗读", "书信"],
  "brief": "朗读下面这封信，录音后提交。\n\nDear Miss Chen, thank you for helping me with my English last term. …",
  "materials": [
    {"label": "朗读示范音频", "who": "平台（TTS 合成）", "app": "speech",
     "purpose": "给学生一段标准范读",
     "shell": "speech say \"Dear Miss Chen, …\" --speaker us_annie --language 2 --speed 1.0 --out materials/01-demo.mp3",
     "saves_as": "materials/01-demo.mp3",
     "handoff": "把这段文字念成音频，美式发音，原速"},
    {"label": "学生朗读录音", "who": "学生", "app": "student",
     "saves_as": "materials/01-recording.mp3"}
  ],
  "steps": [
    {"n": 1, "label": "口语评阅", "application": "oral-review", "command": "oral review",
     "purpose": "给这段朗读打分并给出发音反馈",
     "shell": "oral review materials/01-recording.mp3 \\\n    --content \"Dear Miss Chen, …\" --ques-type 1",
     "outcome": "百分制总分 + 逐词发音建议",
     "handoff": "评阅这段朗读录音，朗读原文是那封感谢信"}
  ],
  "notes": ["示范音频是标准发音，评阅分数基本都偏高——这条任务的价值在给学生一段范读。"]
}
JSON
```

一次好几条就用 `--dir`：把几个 `.json` 放一个目录，
`tasks add --dir ./written/`。**整批先全验一遍再一起写**——一条坏的不会让
半批脏数据落盘，报错会指名道姓是哪个文件。

### ④ 验一遍、交给 guide

```bash
bash "$S/scripts/run.sh" tasks check                 # 全验（推荐每次写完都跑）
bash "$S/scripts/run.sh" tasks handoff 01            # 打「交给 guide 的话」
bash "$S/scripts/run.sh" tasks catalog --write       # 生成 清单.md
```

`check` 会验：结构齐不齐、应用名认不认识、**shell 能不能解析**、
**引用的 `materials/xx` 前面有没有产出过**、同一套里标题有没有撞、
TTS 音色在不在已实测的名单里。

## 任务的 JSON 形状

| 字段 | 说明 |
| --- | --- |
| `taskNo` | 编号，两位。**不给会自动分配** |
| `title` | **像真题一样的标题**，别叫"XXX 测试" |
| `application` | 执行交给哪个应用（见题型的「执行交给」） |
| `brief` | **学生看到的那段话**，含题面全文 |
| `materials[]` | 素材：`label` / `who`（谁来做）/ `app` / `shell` / `saves_as` / `handoff` |
| `steps[]` | 执行：`label` / `application` / `command` / `shell` / `outcome` / `handoff` |
| `notes[]` | 这条任务必须让人知道的（平台限制、诚实说明） |

两条硬要求：

- **素材交给平台产出的，必须写 `saves_as`** —— 执行那一步要知道文件在哪。
- **`shell` 要写出来**，不能只说"用 speech"：`check` 会拿去 `shlex` 解析，
  引号配平、引用的文件存在，都在这一关拦。

`materials` 里 `app` 是 `student` / `teacher` 的表示人工准备，不用写 `shell`。

## 把任务交出去

```bash
bash "$S/scripts/run.sh" tasks handoff 01
```

把这些话交给 `/unipus-aigc:guide`（或直接调用对应应用 skill）。**素材那几步
也一样**——"把这段文字念成音频"就是交给 `speech`。

> **本 skill 只出清单，不执行。** 不要在这里替用户跑任务、也不要自己去调
> `speech` / `review` 那些接口——那是 `guide` 和各应用 skill 的职责。
> **整个 `tasks` 域不构造 client**，一条平台请求都不发。

## 凭证（token）

**什么都不用配。** `tasks` 的所有子命令都**不发平台请求**，没配过凭证照样能用。

> **本 skill 不管登录。** 不要在这里向用户索要账号密码，也不要引导他手动粘
> JWT——那是 `/unipus-aigc:guide` 的职责。只有当用户想在**别的 skill** 里跑
> 任务、而那边报凭证错时，才让他走 `/unipus-aigc:guide`。

## 三条必须对用户说清楚的（都是平台属性，不是缺陷）

1. **朗读评测的分数天然偏高。** 示范音频是 TTS 合成的**标准发音**，
   拿它去评阅实测 95–98。这条任务的价值在「给学生一段范读」和
   「看反馈里该怎么读」，**不在测出学生多差**。写 `notes` 时要如实说。
2. **阅读/听力练习的「答题」没有平台接口。** 文档里的 `ques/ans` 实测 404，
   学生作答只能人工收，平台不判分。
3. **阅读材料建了就删不掉。** 平台没有 `rm/delete`。所以出题那类任务适合
   按学期规划好条数再跑，别随手试。

另外两条涉及素材的：**出图只能用 `general_v2.1_L`**（文档点名的风格会被平台
静默改写后失败）；**翻译评阅只给分、不产出译文**。

## 范围之外

- 本 skill **不由 `guide` 路由**——用户直接 `/unipus-aigc:task-gen` 唤起。
- 想直接用某个应用（翻译一段文字、画一张图、就文档提问……）→ 让他运行
  `/unipus-aigc:guide`。
- 本 skill **不给任务判分**：清单里写的是"期望产出"，学生交上来的东西对不对
  归评阅类应用管。

## 更深的材料

题型要求、素材从哪来、每条注意事项的依据，都在 plugin 仓库的
`docs/call-chains.md`（§12 是任务生成）。
