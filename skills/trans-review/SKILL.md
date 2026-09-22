---
name: trans-review
description: >-
  Score a Chinese/English translation through the Unipus AIGC platform — hand it
  the source text plus a candidate translation and get back a 0–100 score
  (翻译评阅 / 译文打分 / 这段翻译翻得怎么样 / 翻译质量评分). Use when the user has
  a translation and wants it graded. **This skill does NOT produce or improve a
  translation** — for that use the translate skill. Covers scoring, reading the
  result, and looking up / deleting the records it creates.
  通过 Unipus AIGC 平台做翻译评阅：给原文 + 待评译文，返回百分制分数。
---

# Unipus AIGC · 翻译评阅

给一份**中文/英文的译文**打分，返回一个百分制分数。**异步任务但很快**：
提交拿到 `taskId`，通常几秒内出分。

## 最要紧的一句话：它只打分，不改译文

**这个应用不产出、也不修改译文。** 结果里只有一个分数，一个字的译文都没有。

用户要的是"帮我润色这段译文 / 改得地道一点 / 译后编辑" → **别用这个 skill**，
用 `translate`（`translate text` / `translate submit-doc`）。

用户要的是"这段翻译翻得怎么样 / 能打多少分 / 帮我评一下" → 这个 skill。

历史上本仓库把它错记成"译后编辑"，那是个**错误的名字**，已经改掉了
（前端标题写死 `翻译评阅`，接口文档的枚举也是 `36 //翻译评阅`）。
**不要沿用"译后编辑"这个说法**去跟用户解释。

## 先问清楚三件事

1. **题目原文是什么** —— 翻译题的原句。用户没给就问他要，别自己编一段：
   原文参与打分（实测：换一段不相干的原文，分数会从 97.66 掉到 7.9）。
2. **待评的译文是什么** —— 要被打分的那份译文。
3. **语言对** —— `en→zh` 还是 `zh→en`。默认 `en→zh`（`--src-lang en --tgt-lang zh`）。
   拿不准就问，**别猜**：语言对弄反，平台不报错，直接给 0 分。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" trans-review <子命令> [参数...]
```

（`trans-review` 可以简写成 `tr`。）

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
bash "$S/scripts/run.sh" trans-review review \
  --src-text "The quick brown fox jumps over the lazy dog." \
  --tgt-text "敏捷的棕色狐狸跳过了那只懒狗。"
```

长文本用文件：

```bash
bash "$S/scripts/run.sh" trans-review review \
  --src-file src.txt --tgt-file tgt.txt --src-lang zh --tgt-lang en
```

默认等 120 秒。stdout 是可读的结果，完整原始 JSON 落在
`~/.cache/unipus-aigc/trans-review-<taskId>.json`（路径会打到 stderr）。
加 `--json` 同时把原始结果打到 stdout。

输出长这样：

```text
评阅得分: 97.66
记录 wmId: 21…   （删记录用这个，不是 taskId）
耗时     : 0.2707 s
uuid     : ccb9a368-…

注：本 operation 是**翻译评阅**，只给分数，**不产出改后的译文**。
    要译文请用 `translate text` / `translate submit-doc`。
```

## 语种码：写错不报错，只给假分数

**这是这个应用最坑的地方，务必按下面的来。**

`--src-lang` / `--tgt-lang` 只认**小写两字母** `en` / `zh`，
而且**必须跟记录上的语言对一致**。写错了平台**一次错都不报**——
回的是 `status=3`、`code:"200"`、`message:"Success."`，只是分数变成 0.0
或者一个 42.58 那样的假数。实测矩阵（记录为 `en → zh`，只动语种码）：

| `srcLang`/`tgtLang` | 分数 | 判读 |
| --- | --- | --- |
| `en` / `zh` | 97.66 | 正常 |
| `en` / `zho` | 42.58 | 走了另一条错路 |
| `eng` / `zho` | 42.58 | 同上 |
| `EN` / `ZH` | 42.58 | 同上 |
| `zh` / `en` | 0.00 | 语言对反了 |
| `en` / `ja` | 0.00 | 不支持的目标语 |
| `en` / `en` | 0.00 | 同语 |

**语言对由记录上的 `langFrom`/`langTo` 说了算**，不是提交时说了算。
所以在 `en → zh` 的记录上哪怕交一份再好的英文，只要写 `--tgt-lang en`，也是 0 分。
要评另一对语种请**新建记录**（不要传 `--wm-id`）。

CLI 已经替你把关：`--src-lang zho` 这类会在**本地**就被挡成退出码 `2`，
`--wm-id` 跟语种对不上也会本地报错、**不发到平台**。看到这两类报错，
改参数就行，不是平台坏了。

## 分数怎么读

- `score` 是**百分制浮点**，同一份输入**可复现**（连跑两次都是同一个 97.66）。
- 实测参考：通顺的译文 97.66，刻意生硬的 90.56，与原文完全不相干的 8.82。
- 除 `score` 外只有 `uuid` / `code` / `message` / `waiting_jobs_num` / `time_cost`，
  **没有任何译文**。

## 查询与重入

```bash
bash "$S/scripts/run.sh" trans-review submit --src-file s.txt --tgt-file t.txt  # 只提交
bash "$S/scripts/run.sh" trans-review poll <taskId> --wait 60   # 短轮询，可反复调用
bash "$S/scripts/run.sh" trans-review get <taskId>              # 只查一次
bash "$S/scripts/run.sh" trans-review detail <wmId>             # 记录详情
```

- 退出码 `0` → 完成。
- 退出码 `3` → **还在跑，不是失败**。用**同一个 taskId** 再 poll 一次。
  不要重新 submit（会产生重复记录）。
- 退出码 `1` → 真失败。**taskId 不存在也是退出码 1**（`code=3001 当前任务不存在`），
  跟"还没出结果"的退出码 3 是两回事——别把输错的 id 当"再等等"。

`detail` 这条路是**有结果的**，而且这个应用的 `evaluation` 在**顶层**（已填好）——
跟作文评阅（顶层恒为 null）、口语评阅（埋在 `evaluationList[0]`）都不一样。
`detail` 里那个 `translation` 字段是**提交时那份待评译文的原样存档**，
不是平台改过的——**别读成"改后的译文"**。

## 记录与清理

```bash
bash "$S/scripts/run.sh" trans-review records     # 列记录（type="2"）
```

**重要：这条链路的记录 `cleanup` 看不见。** `cleanup` 只扫作文评阅（`type="1"`），
翻译评阅是 `type="2"`，两边不是同一个列表。清理要走：

```bash
bash "$S/scripts/run.sh" trans-review delete <wmId>     # 不带 --yes 只列不删
```

用记录里的 **`wmId`**（不是 `taskId`）。

## 收尾

评阅会在平台上留记录，而且**提交失败也可能留下一条空记录**
（`wm/create` 跑在任务提交前面——报错看得见，垃圾数据也留下来了）。
用户想清理时**不要自己动手**：`trans-review delete` 不带 `--yes` 只列不删，
**任何情况下都不要替用户按 `--yes`**，也不要在话术里引导用户"直接加 `--yes` 就行"。
用户执意要删，让他自己确认。

## 范围之外

用户问到这个应用之外的平台能力（"能不能评作文"、"还能干什么"）时，
**不要猜、也不要直接说没有**——让他运行 `/unipus-aigc:guide`，
那里有平台应用的完整清单和可做性分档。

## 更深的材料

这条链路和实测细节（为什么它不叫译后编辑、`subType:70` 为什么不参与计算、
语种码的完整对照矩阵、`wm/detail` 在三种评阅下的层级差异）记在 plugin 仓库的
`docs/call-chains.md` §8。改这个应用之前先读那一节。
