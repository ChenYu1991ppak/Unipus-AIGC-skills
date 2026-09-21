---
name: question-gen
description: >-
  Generate questions from a reading passage through the Unipus AIGC platform —
  build a 阅读材料, generate 单选题/多选题/判断题/问答题/排序题 with the
  智能出题 strategies, review the generated items, and 采纳 (adopt) the good
  ones. Use when the user asks for 出题 / 智能出题 / 根据文章出题 / 生成练习题 /
  生成阅读理解题 / 组卷题目 / 把这篇材料变成题目. Also covers listing existing
  reading materials and adopted questions.
  通过 Unipus AIGC 平台从阅读材料出题、审题、采纳。
---

# Unipus AIGC · 出题

一条链路：**阅读材料 → 出题 → 审题 → 采纳 → 取题面**。

## 先问清楚两件事

1. **拿哪篇材料出题** —— 用户给的文章，还是平台上已有的？
   已有的先 `questions materials` 看一眼，**能复用就复用**（原因见下）。
2. **要什么题、几道** —— 从下面的策略表里挑。用户只说"出几道题"时，
   默认 `1010:5`（事实细节-选择题 5 道），并**告诉他你选了什么**。

## ⚠️ 这条链路上三个必须知道的坑

**1. 阅读材料建了就删不掉。**

`rm/delete` **不存在**——全文档 4518 行扫描确认 `rm` 只有
`create` / `detail` / `update` / `list` 四个端点。所以
`create-material` **是不可逆操作**，别为了"试一下"随手建。
出题侧唯一的删除是 `questions delete`（按 `quesId`，**只删题目，不删材料**）。

> 已经有一条长期材料时就用它。要建新的，先把正文长度和去向给用户看一眼。

**2. 出题有两条路，只有一条能用。**

| | `preview`（§2.1 `ques/generation`） | `generate`（op12） |
| --- | --- | --- |
| 返回 | 秒级，题面直接在响应里 | 异步任务，要轮询 |
| 落库 | **不落库** | 落库，`generateCount` 加一 |
| `quesId` | **没有** | **每题都有** |
| 能采纳吗 | **不能** | 能 |

`preview` 出的题**采纳不了**（`ques/accept` 要的 `questionId` 就是 `quesId`，
它没有）。它的用处是**不留残留地试策略组合**。
真要出题走 `generate`。

**3. `questions accept` 的 id 是 `quesId`。**

不是策略 `code`，也不是 `rmId`。拿错会得到 `code=1001 采纳题目不存在`。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" questions <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上的 `NotOpenSSLWarning` 是环境噪声，**只看退出码判断成败**。

凭证没配或过期时（退出码 1）→ 让用户走 `/unipus-aigc:guide`。

## 策略表

```bash
bash "$S/scripts/run.sh" questions ploys
```

三档难度 × 三种题型，`code` 就是 `--ploy` 要的值：

| 难度 | 选择题 | 判断正误题 | 简答题 |
| --- | --- | --- | --- |
| 事实细节 | `1010` | `1011` | `1012` |
| 推断 | `1020` | `1021` | `1022` |
| 主旨大意 | `1030` | `1031` | `1032` |

`--ploy 1010:3` 表示"事实细节-选择题 3 道"，可重复给多组。

**这套 `code` 跟 `rm/create` 的 `subType`（5/24/25/26/27/28）不是一套编号**，
跟 op37 智能出排序题的 `code`（1014/1024/1034）也不是——别互相套用。
`questions ploys` 会把三套都列出来对照。

## 出题

```bash
bash "$S/scripts/run.sh" questions generate <rmId> --ploy 1010:3 --ploy 1022:2
```

op12 走 `task/submit` + 轮询，**出题不快**（实测几十秒到两分钟）。
`--wait` 默认 180 秒；超时是退出码 **3，不是失败**，任务还在平台上跑，
用同一个 `rmId` 查 `questions record --rm-id <rmId>` 就能拿到结果。

stdout 每行一题，带 `quesId`——**这就是采纳要的 id**，记下来给用户看。

## 审题与采纳

```bash
bash "$S/scripts/run.sh" questions record --rm-id <rmId>       # 最新一次出的题（含题干全文）
bash "$S/scripts/run.sh" questions records <rmId>              # 出题历史，拿 pid
bash "$S/scripts/run.sh" questions record --pid <pid>          # 指定某一次
bash "$S/scripts/run.sh" questions accept <quesId>             # 采纳
bash "$S/scripts/run.sh" questions accept <quesId> --cancel    # 取消采纳
bash "$S/scripts/run.sh" questions accepted <rmId>             # 已采纳的题
```

**把题目念给用户听、让他挑。** 不要自己决定采纳哪些——采纳是内容判断，
只有用户知道哪道题合适。

采纳完整批之后可以取组装好的题面：

```bash
bash "$S/scripts/run.sh" questions json <rmId>
```

它返回「原文 + 采纳的题」拼成的树。**一条都没采纳时接口报
`1001 没有采纳题目`**——那表示还没采纳，不是出错。

## 建阅读材料

```bash
bash "$S/scripts/run.sh" questions materials                  # 先看看有没有现成的
bash "$S/scripts/run.sh" questions create-material --path /绝对路径/a.txt
bash "$S/scripts/run.sh" questions create-material --text "短文内容..."
echo "短文" | bash "$S/scripts/run.sh" questions create-material
```

`--education` 默认 `5`（本科），出题场景 `--sub-type` 用 `5`（智能出题）。
stdout 是 `rmId`。

**建之前先跟用户确认**：这是不可逆的，而且正文会进平台库。

## 删题目

```bash
bash "$S/scripts/run.sh" questions delete <quesId> [<quesId>...]        # 只列不删
bash "$S/scripts/run.sh" questions delete --rm-id <rmId>                # 该材料下已采纳的题
```

**不带 `--yes` 只列不删。** 确认无误后**由用户自己**加 `--yes` 重跑——
不要替他按，也不要引导他"直接加 `--yes` 就行"。

> 删的是题目。阅读材料（`rmId`）**没有删除接口**，删不掉。

## 范围之外

用户问到出题之外的平台能力（"能不能做 PPT"、"还能干什么"、"有作文评阅吗"）时，
**不要猜、也不要直接说没有**——让他运行 `/unipus-aigc:guide`，那里有平台应用的
完整清单和可做性分档。

## 更深的材料

这条链路混合了两种通道（`rm`/`ques` 多数是普通 HTTP，只有 op12 是异步任务），
是"同一个业务跨两种通道"的样本。要照着它实现新应用时，读 plugin 仓库的
`docs/call-chains.md`。
