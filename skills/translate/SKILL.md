---
name: translate
description: >-
  Translate text or documents through the Unipus AIGC platform.
  Use when the user asks to translate a passage of text, or a file such as
  docx/pdf/pptx/txt, between languages — most commonly Chinese <-> English.
  Long document jobs are split into submit + poll. 通过 Unipus AIGC 平台做文本
  翻译与文档翻译；文档翻译是长任务，走 submit/poll 两步。
---

# Unipus AIGC · 翻译

两条链路：**文本翻译**（秒级，一次调用搞定）和**文档翻译**（长任务，必须拆两步）。

## 先问清楚三件事

1. **翻什么** —— 一段文字，还是一个文件？文件的话要绝对路径。
2. **从什么语种翻到什么语种** —— 用户没说就按内容判断，并**告诉他你选了什么**。
3. **文档翻译要不要落盘** —— 要的话问清楚存到哪个路径。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" translate <子命令> [参数...]
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


## 文本翻译：一次调用

```bash
bash "$S/scripts/run.sh" translate text "要翻译的内容" --from en --to zh
```

通常 1~3 秒出结果，stdout 直接是译文。加 `--json` 同时打印原始结果。

## 文档翻译：必须 submit + poll 两步

**不要用一次阻塞调用等文档翻译。** 前台命令有 600 秒上限，而文档翻译耗时不可预测
（小文件实测 10 秒内，大文件可能几分钟到十几分钟），阻塞等一定会被砍掉、什么都拿不到。

**第 1 步 · 提交**（秒级返回记录 id）：

```bash
bash "$S/scripts/run.sh" translate submit-doc /绝对路径/a.docx --from en --to zh
```

输出里有 `记录 id`。**把这个 id 记下来**——状态存在平台侧，中断了也能用它重入。

**第 2 步 · 轮询**（可反复调用，每次最多等 `--wait` 秒）：

```bash
bash "$S/scripts/run.sh" translate poll <记录id> --wait 120 --out /绝对路径/a.zh.docx
```

- 退出码 `0` → 完成。stdout 有 `译文地址`，`--out` 给了就已落盘。
- 退出码 `3` → **还在跑，不是失败**。告诉用户"仍在处理中"，隔一会儿用**同一个 id**
  再 poll 一次。不要重新 submit（会产生重复记录）。
- 退出码 `1` → 真失败，stdout/stderr 里有平台给的原因（`msg`）。

只想看一眼当前状态、不等待：

```bash
bash "$S/scripts/run.sh" translate get <记录id>
```

## 最大的坑：语种码有两套

**文档翻译只吃两字母码，任何一侧用三字母码都会立刻失败。**

| 调用 | `en`/`zh`、`cn`/`en` 这类两字母 | `eng`/`zho` 这类三字母 |
| --- | --- | --- |
| 文本翻译 | OK | OK |
| **文档翻译** | **OK** | **立刻失败** |

失败长这样，而且是**提交后瞬间**就失败：

```jsonc
{ "status": 4, "msg": "文档翻译失败",
  "flowResponses": [{ "status": 0 }] }
```

**你不需要自己换算**——CLI 已经按调用类型选对了词表（文档走两字母、文本走三字母）。
但**用户报"文档翻译失败"时，第一件事就是确认语种**：只实测过 `en` ↔ `zh`。

**为什么会有两套：** 文档翻译的上游是**百度翻译**（成功记录的 `msg` 里带
`fanyidoc.cdn.bcebos.com` 的地址），百度用 `en`/`zh` 这套码。
所以**文档翻译支持的语种范围由百度决定**，跟平台 `translate/lang/list`
返回的 202 个语种（全是三字母码）**不是一回事**。用户要翻小语种文档时，
先说清楚这一点，别承诺。

## 第二个坑：`status` 只能判失败，不能判完成

| 场景 | 顶层 `status` | `translateUrl` / `translation` | `flowResponses[].status` |
| --- | --- | --- | --- |
| 进行中 | `1` | 都是 `null` | `0` |
| 成功 | `1` | **非空** | `1` |
| 失败 | **`4`** | `null` | `0`（**不是 4**） |

- **判完成**只看 `translateUrl`（文档）或 `translation`（文本）是否非空。
  拿 `status == 1` 判完成，第一轮就会拿到一个 `translation: null` 的半成品。
- **判失败**看顶层 `status == 4`，原因在 `msg`。
- `flowResponses[].status` **不可靠**，失败时它是 `0`。别拿它判失败，
  否则一个死掉的任务会被当成"仍在处理中"无限轮询。

CLI 里这些都已经处理好了。只有你要绕过 CLI 直接读接口时才需要记住这张表。

## 结果的存放位置

每次成功都会把原始 JSON 落到 `~/.cache/unipus-aigc/translate-<id>.json`，
路径打在 stderr 上。要完整结构就去读那个文件，不要反复调接口。

`--out` 是**译文文件**的落盘路径（docx 等）；文本翻译的结果直接在 stdout。

## 收尾

翻译会在平台上留记录。用户想清理时**不要自己动手**——
让他走 `/unipus-aigc:guide` 的 `records` / `cleanup`，那里有安全约束。

注意 `translate/list` **不返回纯文本翻译记录**，所以文本翻译的残留是看不见的。

## 范围之外

用户问到翻译之外的平台能力（"平台上有没有做 PPT 的"、"还能干什么"、
"口语评阅支持吗"）时，**不要猜、也不要直接说没有**——转给对应的 skill：

| 用户想要 | 去哪 |
| --- | --- |
| **评价**一段译文翻得好不好（打分） | `unipus-aigc:trans-review`——**只回一个分数，不产出译文** |
| 评价一段朗读音频 | `unipus-aigc:oral-review` |
| 评价一篇英语作文 | `unipus-aigc:review` |
| 平台的完整应用清单 | `/unipus-aigc:guide`（25 个应用 + 可做性分档） |

> 这条区分最容易搞混：**"翻译"是产出译文，`trans-review` 是给译文打分。**
> 用户说"帮我改改这段译文 / 润色一下"时留在本 skill；说"这段翻得怎么样 /
> 给我打个分"时去 `trans-review`。别把后者当成本 skill 的一个参数。

## 更深的材料

逐条调用链、七牛上传细节、字段名踩坑表，见 plugin 仓库的 `docs/call-chains.md` §1。
翻译评阅（打分）见 §8，口语评阅见 §7。
