---
name: oral-review
description: >-
  Score a spoken-audio recording through the Unipus AIGC platform — upload an
  audio file, get back an overall score plus a per-word pronunciation report
  (口语评阅 / 朗读评测 / 发音打分 / 给这段录音评分). Use when the user hands
  you an audio file and asks how well it was read aloud, or wants pronunciation
  feedback on a recording. Covers submitting the audio, polling the async task,
  reading the report, and looking up / deleting the records it creates.
  通过 Unipus AIGC 平台做口语评阅：音频评分 + 逐词发音反馈。
---

# Unipus AIGC · 口语评阅

给一段**朗读音频**打分，返回总分 + 逐词发音反馈。**异步任务**：
提交拿到 `taskId`，再用同一个 id 查结果（不像语音合成那样一次调用就能拿完）。

## 先问清楚两件事

1. **音频文件在哪** —— 本地路径，或者已经在七牛上的 URL。没有音频就做不了。
   音频**没有**别的必填项（时长、语速都不用给）。
   但有**时长上限**，而且上限跟着 `--ques-type` 走（20 秒 / 60 秒 / 无）——
   见下面的表。**先确认这段音频有多长，再挑 quesType**，别反过来。
2. **朗读原文是什么** —— 这段录音照读的那段文字。**实测服务端必填**
   （见下面「两个实测必填项」）。用户没给就**问他要**，别自己转写一段充数：
   评测内容跟音频对不上，反馈就是错的。

题目类型、学段、语言类型都是可选的，用户不提就别问（`--ques-type` 有默认值）。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" oral <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上的 `NotOpenSSLWarning` 是环境噪声，**只看退出码判断成败**。

## 凭证（token）

**先直接跑，别先问凭证。** 续期是自动的——只要之前配过账号密码，
token 失效时**会自己换新**，你和用户都不需要做任何事。

只有命令**真的失败**之后才需要处理。先看一眼现状：

```bash
bash "$S/scripts/run.sh" token        # 退出码 0 = 可用；1 = 过期或没配
```

按结果分两种，**两种都是把用户送去 `/unipus-aigc:guide`**：

| `token` 的结果 | 怎么做 |
| --- | --- |
| 退出码 `1` + `未找到 JWT` / 从没配过 | 用户**没有**账号密码 → 让他运行 `/unipus-aigc:guide`，那里会问他要账号密码 |
| 退出码 `1` + `EXPIRED`，但配过账号密码 | 自动续期没成功（rt 过期 / 密码改了）→ 也让他走 `/unipus-aigc:guide` 重新给一次 |

> **本 skill 不管登录。** 不要在这里向用户索要账号密码，
> 也不要引导他手动粘 JWT——那是 `/unipus-aigc:guide` 的职责，
> 它有一条可以照做的样例。

### 平台返回 401 / 「用户登录失效」时

同一个道理：先跑 `token` 确认，再路由到 `/unipus-aigc:guide`。
**不要自己重试、不要猜是不是参数问题**——凭证失效和参数错误的表现完全不同。


## 评阅：一次调用

```bash
bash "$S/scripts/run.sh" oral review ./a.mp3 --content "I went to school yesterday by bus."
```

默认等 300 秒。通常几秒到几十秒出结果，stdout 是**可读的评阅报告**，
完整原始 JSON 落在 `~/.cache/unipus-aigc/oral-<taskId>.json`（路径会打到 stderr）。
加 `--json` 同时把原始结果打到 stdout。

## 两个实测必填项（文档没标，别踩）

**1. `--content`（朗读原文）** —— 不给会报
`code=100 evaluationContent字段不能为空`。

**2. `--ques-type`（题目类型）** —— 不给会报
`code=100 quesType字段不能为空`。CLI 默认填 `5`（篇章测评）。

**有效值只有 `{1, 3, 5}`**，而且**`1` 不是「朗读短文」**——那是早先照 CLI 帮助
文本抄的，实测不成立。三者的区别看**时长上限**：

| 值 | 题型 | 音频时长上限 | 结果里的拆解 |
| --- | --- | --- | --- |
| `1` | 单词测评 | **20 秒** | `evaluation.words[]` |
| `3` | 句子测评 | **60 秒** | `evaluation.words[]`（扁平逐词） |
| `5` | **篇章测评** | 没撞到上限（72.9 秒通过） | `evaluation.sentences[]`，每句内嵌 `words[]` |

别的值全不收：`0/2/4/6/7/8/10/11/12` → `code=100 quesType字段值不支持`；
`9` → `code=500 server error!`。

**平台没有一个专门给「朗读短文」的取值。** 朗读练习的素材通常是 40～70 秒，
`1` 和 `3` 的上限都装不下，只能走 `5`——这也是默认值给 `5` 的原因。
超时长度的报错会把题型名念出来（`单词测评，音频时长不能超过20秒`），
拿不准当前是哪个题型时，这是最省事的识别办法。

关于第 2 条有个坑值得知道：报错路径是 `task/submit`，**但字段要写在 `wm/create`
上**——平台是顺着 `wmId` 回记录里读这个值的，塞进 `submitData` 没用。

## 音频格式

接受 `.mp3` `.wav` `.ogg` `.m4a` `.aac` `.flac`（以及任何 MIME 以 `audio`
开头的文件）。**别的基本都不收**，先转格式再传。

## 结果怎么看

报告长这样：

```text
总分  : 98.0
音频时长: 3744 ms
评阅人: 学生1
记录 id: 21…   （删记录用这个，不是 taskId）
识别结果: I WENT TO SCHOOL YESTERDAY BY BUS
--- 发音反馈 ---
1. **"TO" 的发音**：…
…（逐词列，结尾一段"整体建议"）
```

- **总分**在 `evaluation.overall`，百分制。
- **音频时长**是**毫秒**（`3744` 对应一句话），不是秒。
- **逐词/逐句的发音反馈在那段 `feedback` 文本里**——按词列，结尾一段
  「整体建议」。

**不要去找 `errorSegments` / `pronunciationIssues` 这类结构化字段**，
逆向出来的前端代码里那两个名字是**展示层的变量名**，不在传输层。
平台返回的是一整套字段骨架（`fluency` / `pronunciation` / `integrity` /
`relevance` / `grammar` / `sentences` / `words` / …），**哪些被填上取决于
`--ques-type`**：实测 `3` / `5` 会把 `fluency` / `pronunciation` / `integrity`
/ `overall` / `speed` / `duration` **全填成真数值**，并给出逐词（`3`）/ 逐句
（`5`，句内再嵌 `words[]`）的拆解。
**没被填的字段才是 `null`**——报告里会点一句「为 null，不是 0 分」，
**别把 null 读成"这一项评了 0 分"**。

## 查询与重入

```bash
bash "$S/scripts/run.sh" oral submit ./a.mp3 --content "…"   # 只提交，返回 wmId + taskId
bash "$S/scripts/run.sh" oral poll <taskId> --wait 60        # 短轮询，可反复调用
bash "$S/scripts/run.sh" oral get <taskId>                   # 只查一次
bash "$S/scripts/run.sh" oral detail <wmId>                  # 记录详情（见下）
```

- 退出码 `0` → 完成。
- 退出码 `3` → **还在跑，不是失败**。用**同一个 taskId** 再 poll 一次。
  不要重新 submit（会产生重复记录、重复计费）。
- 退出码 `1` → 真失败。**taskId 不存在也是退出码 1**（`code=3001 当前任务不存在`），
  跟"还没出结果"的退出码 3 是两回事——别把输错的 id 当"再等等"。

**`detail` 这条路是有结果的**（跟作文评阅相反）：`oral detail <wmId>` 能拿到
完整的 `evaluation`，还多一个 `evaluationStatus`（`1`/`2` 评阅中、`3` 成功、
`4`/`8`/`9` 不出结果）。注意这是**记录行的编号，跟 `queryTask` 的 7 态不是同一套**，
别对着读。

## 记录与清理

```bash
bash "$S/scripts/run.sh" oral records     # 列记录（type="3"）
```

**重要：这条链路的记录 `cleanup` 看不见。** `cleanup` 只扫作文评阅（`type="1"`），
口语评阅是 `type="3"`，两边不是同一个列表。清理要走：

```bash
bash "$S/scripts/run.sh" oral delete <wmId>          # 不带 --yes 只列不删
```

用记录里的 **`wmId`**（不是 `taskId`）。

## 收尾

评阅会在平台上留记录，而且**提交失败也可能留下一条空记录**（`wm/create`
跑在任务提交前面——报错看得见，垃圾数据也留下来了）。用户想清理时
**不要自己动手**：`oral delete` 不带 `--yes` 只列不删，
**任何情况下都不要替用户按 `--yes`**，也不要在话术里引导用户"直接加 `--yes` 就行"。
用户执意要删，让他自己确认。

## 范围之外

用户问到这个应用之外的平台能力（"能不能评作文"、"还能干什么"）时，
**不要猜、也不要直接说没有**——让他运行 `/unipus-aigc:guide`，
那里有平台应用的完整清单和可做性分档。

## 更深的材料

这条链路和口语评阅的实测细节（结果字段的真实形状、`wm/create` 为什么不传
`subType`、`quesType` 为什么要落在 `wm/create` 上）记在 plugin 仓库的
内部的接口记录 §7。改这个应用之前先读那一节。
