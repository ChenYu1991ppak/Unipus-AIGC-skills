---
name: task-gen
description: >-
  Create teaching tasks as a task list for the Unipus AIGC platform — 英语朗读
  评测 / 英语写作 / 阅读理解练习 / 翻译练习 / 看图作文 / 听力练习 each written
  like a real classroom task, with the materials it needs (示范音频、阅读材料、
  插图) noted as their own steps to be produced by other apps. A task list covers
  many applications and can be handed to guide for execution. Use when the user
  asks for 生成任务 / 造任务 / 任务清单 / 出一批练习 / 出一套题 / 组卷 /
  教学任务 / 布置作业 / 阅读练习 / 写作练习 / 翻译练习 / 朗读任务 /
  听力练习 / exercise / task list. 生成教学任务清单：默认不调用平台。
---

# Unipus AIGC · 任务生成

**产出是一份任务清单**——每条任务都像真实课堂里的一次作业或练习，写清楚：

- **交给哪个应用**去执行（朗读评测 → `oral-review`，作文评阅 → `review`……）
- **素材从哪来**（示范音频交给 `speech` 合成，阅读材料交给 `text-gen` 起草，
  作文正文由学生自己写）
- **期望产出什么**（分数、纠错、题目……）

除了清单本身，**本 skill 不调用任何平台接口**——任务交给 `guide` 去执行。

## 先问清楚两件事

1. **要哪些题型**。现有六种（`tasks list` 可以看）：

   | 题型 | 交给 | 素材由谁产出 |
   | --- | --- | --- |
   | 英语朗读评测 | `oral-review` | 示范音频 ← `speech` |
   | 英语写作 | `review` | （题面在任务里） |
   | 阅读理解练习 | `question-gen` | 阅读材料 ← `text-gen` |
   | 翻译练习 | `trans-review` | （原文在任务里） |
   | 看图作文 | `review` | 插图 ← `image-gen` |
   | 听力练习 | `question-gen` | 脚本 ← `text-gen`，音频 ← `speech` |

2. **每种要几条**（`--count` 是**每种题型**各几条）。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" tasks <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错（**一个请求都没发**）/ `3` 仍在处理中。
stderr 上的 `NotOpenSSLWarning` 是环境噪声。

## 出题（不碰平台）

```bash
bash "$S/scripts/run.sh" tasks list                        # 六种题型
bash "$S/scripts/run.sh" tasks gen --count 4 --seed 42      # 每种 4 条 = 24 条
bash "$S/scripts/run.sh" tasks gen --task oral-drill,essay-writing --count 6
```

落在 `~/.cache/unipus-aigc/tasks/<任务集>/`：

```
清单.md                    一眼看完的目录（按场景分组）
manifest.json              机器可读的清单
tasks/03-reading-comprehension.md    每条任务一张卡（含交接语）
tasks/03-reading-comprehension.json
materials/                 素材取件目录（空目录，等 guide 往里放东西）
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--task <名\|all>` | 题型，逗号分隔，默认 `all` |
| `--count N` | **每种题型**各 N 条 |
| `--seed N` | 给了就完全可复现（同 seed 两次跑，清单逐字节相同） |
| `--speaker <音色>` | 把所有 TTS 素材的音色统一成它（默认随机 `en_luka` / `us_annie`） |
| `--out <目录>` | 换落盘根目录 |

其它：

```bash
bash "$S/scripts/run.sh" tasks sets               # 已有的任务集
bash "$S/scripts/run.sh" tasks show 05            # 看第 5 条（编号或标题片段）
bash "$S/scripts/run.sh" tasks handoff 05         # 只打「交给 guide 的话」
bash "$S/scripts/run.sh" tasks check              # 本地校验（占位符、应用名、路径）
bash "$S/scripts/run.sh" tasks set-speaker us_annie            # 整批换音色（默认最新那套）
```

`--count` 超过素材数会**直接报错**（退出码 2），不会悄悄给你一批重复的。

## 清单里的每条任务长这样

```
# 任务 03　英语朗读评测：A Quiet Morning
交给哪个应用：oral-review　　场景：口语练习　　学段：高中

## 题目
朗读下面这篇短文，录音后提交。系统会给出总分和逐词发音建议。
短文：The library opens at eight in the morning. …

## 素材准备
1. 朗读示范音频   平台（TTS 合成） → materials/03-demo.mp3
2. 学生朗读录音   学生           → materials/03-recording.mp3

## 交给 guide 执行
口语评阅 → oral-review
  评阅这段朗读录音，朗读原文是「The library opens …」
```

**「交接语」是给 `guide` 看的**——照着念或直接粘过去，`guide` 会路由到对应应用。

## 怎么把任务交出去

```bash
bash "$S/scripts/run.sh" tasks handoff 03
```

把这些话交给 `/unipus-aigc:guide`（或者直接调用对应的应用 skill）。**素材那几步
也一样**——比如"把这段文字念成音频"就是交给 `speech`。

> **本 skill 只出清单，不执行。** 不要在这里替用户跑任务、也不要自己去调
> `speech` / `review` 那些接口——那是 `guide` 和各应用 skill 的职责。
> 本 skill 唯一会碰平台的命令是 `tasks set-speaker --check`（现问音色白名单）。

## 凭证（token）

**默认什么都不用配。** `tasks list` / `gen` / `sets` / `show` / `handoff` /
`check` / `set-speaker` 全都**一个平台请求都不发**（也不构造 client），
没配过凭证照样能出题。

只有 `tasks set-speaker --check` 会调平台。那时才需要看凭证：

```bash
bash "$S/scripts/run.sh" token        # 退出码 0 = 可用；1 = 过期或没配
```

按结果分两种，**两种都是把用户送去 `/unipus-aigc:guide`**：

| `token` 的结果 | 怎么做 |
| --- | --- |
| 退出码 `1` + `未找到 JWT` / 从没配过 | 用户**没有**账号密码 → 让他运行 `/unipus-aigc:guide`，那里会问他要账号密码 |
| 退出码 `1` + `EXPIRED`，但配过账号密码 | 自动续期没成功 → 也让他走 `/unipus-aigc:guide` 重新给一次 |

> **本 skill 不管登录。** 不要在这里向用户索要账号密码，
> 也不要引导他手动粘 JWT——那是 `/unipus-aigc:guide` 的职责。

## 三条必须对用户说清楚的（都是平台属性，不是缺陷）

1. **朗读评测的分数天然偏高。** 示范音频是 TTS 合成的**标准发音**，
   拿它去评阅实测 95–98。这条任务的价值在「给学生一段范读」和
   「看反馈里该怎么读」，**不在测出学生多差**。
2. **阅读/听力练习的「答题」没有平台接口。** 文档里的 `ques/ans` 实测 404，
   所以学生作答只能人工收，平台不判分。
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

题型定义、素材从哪来、每条注意事项的依据，都在 plugin 仓库的
`docs/call-chains.md`（§12 是任务生成）。
