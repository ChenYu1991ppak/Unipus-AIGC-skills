---
name: text-gen
description: >-
  Write and generate text through the Unipus AIGC platform — create an article,
  generate titles, build an outline from prose, continue writing, and rewrite a
  passage. Use when the user asks for 写文章 / 帮我写一篇 / 生成标题 / 起标题 /
  写大纲 / 列提纲 / 续写 / 接下去写 / 扩写 / 改写 / 润色这段 / 公众号文案 /
  文本生成 / AI 写作. Also covers listing and deleting articles.
  通过 Unipus AIGC 平台写作：标题、大纲、续写、改写。
---

# Unipus AIGC · 文本生成 / 文章写作

平台**没有**一个叫「文本生成」的应用，这一支是按**能力**落地的：
建文章 → 起标题 → 列大纲 → 续写 → 改写。

## 先问清楚两件事

1. **是"从零写一篇"还是"接着写/改这段"？**
   - 从零写 → 先 `article create` 拿 `articleId`，再 `outline` 出大纲，
     然后按大纲 `continue` 一段段写。
   - 接着写 / 改这段 → 直接 `continue` / `rewrite`，**但都要 `articleId`**，
     所以还是得先建一篇。
2. **什么体裁、给谁看？** 平台有个 `subType`（实测目前只见过 `2` 公众号文案），
   但**它不影响生成质量**，别拿它当体裁开关。

## ⚠️ 这条链路上最重要的一条：文档里那三个端点是死的，别用

接口文档写了 `article/aiTextOperation`（续写/扩写/优化）、
`article/aiOperation`（一级大纲/二级大纲/正文）、`article/aiOptimizeArticle`
（整篇优化），返回表都很具体。**实测三个的 `value.content` 恒为 `null`，而且不看入参**：

* 给真 `articleId` → `{"content": null}`
* 给 `articleId="1"`（**根本没这篇文章**）→ `aiTextOperation` 回**逐字节相同**的
  `{"content": null}`
* 先用 `updateArticle` 把正文真写进去 → **还是** `{"content": null}`

前端产物里**根本搜不到这三个端点**（`uaigc_index.js` 里有 `aiTitle` /
`insertArticle` / `delete`，没有它们）。**所以本 skill 不提供这三个子命令**——
不是因为"还没做"，是因为做了只会给你一个永远为空的字段。

> **真正在用的是另一族 `lm/*`，本 skill 用的就是它。**

## ⚠️ 第二条：`lm/*` 是 **SSE 流式**，不是 socket 推送

上一版把这族记成"要消费 Socket.IO 增量推送"，**那个猜测是错的**。
实测是普通的 **SSE over HTTP**：`content-type: text/event-stream`，
帧形如 `data:{"choices":[{"delta":{"content":"1"}}]}`，`[DONE]` 收尾。

CLI 已经把这层封好了：默认**拼好整段再打印**，加 `--raw` 可以边收边打（看流式效果）。

> ⚠️ 响应头**不带 charset**。不把 `resp.encoding` 钉成 utf-8，中文会整段变成
> `ä¸æ`——跟 v2 RAG 流式是同一个坑，SDK 里已经钉死了。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" article <子命令> [参数...]
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


## 一、建文章

```bash
bash "$S/scripts/run.sh" article create --title "AI 与教育"
```

stdout 是 `articleId`。**唯一硬要的字段是一个 13 位时间戳，SDK 自己生成**——
不用先去页面上建。

**这条链路跟出题那条不一样：`article/delete` 存在，能删干净。**
但删之前先把 id 交给用户看一眼。

## 二、起标题

```bash
bash "$S/scripts/run.sh" article title --article-id <id> -t 1     # 按话题方向
bash "$S/scripts/run.sh" article title --article-id <id> -t 2 --old-title "旧标题"
bash "$S/scripts/run.sh" article title --article-id <id> -t 3 --text "正文…"
```

回 **10 条**。

> ⚠️ **`--article-id` 一定要给。** 不给自己一条都不报错，但出的标题跟你的文章
> 毫无关系（实测：同一篇文章，不给 id 时回的是「如何让生活更高效」这类泛标题）。
> **不报错 ≠ 结果对**——这是本链路最容易吃亏的一处。

## 三、列大纲（唯一非流式的那个）

```bash
bash "$S/scripts/run.sh" article outline <id> --path outline_src.txt
bash "$S/scripts/run.sh" article outline <id> --text "人工智能正在改变教育…"
```

回 **markdown 文本**。把原文给它就行，它自己抽大纲。

## 四、续写与改写（流式）

```bash
# 续写：给"前文"和"后文"，它在中间接着写
bash "$S/scripts/run.sh" article continue <id> \
    --start "人工智能正在改变教育。" --end "它让个性化学习成为可能。"
bash "$S/scripts/run.sh" article continue <id> --start "…" --end "…" --raw   # 边收边打

# 通用续写：**不依赖文章**（适合给任意一段文字接一句）
bash "$S/scripts/run.sh" article common-continue \
    --before "人工智能正在改变教育。" --after "它让个性化学习成为可能。"

# 改写一段
bash "$S/scripts/run.sh" article rewrite <id> --content "这段要改的话…" --method 1
bash "$S/scripts/run.sh" article rewrite <id> --content "…" --prompt "更正式一些"
```

三个都是流式，默认拼好整段再打印。`--wait` 是整条流的上限（默认 120 秒）。

> `rewrite` 的 `fullContent` **不给会报 `code=100 文章内容不能为空`**
> （实测）。CLI 默认拿 `--content` 顶上（前端是两个都给）。
> `--method` 实测 `1` / `2` 都能出文本，**完整取值表没拿到**，别硬编。

## 五、查与删

```bash
bash "$S/scripts/run.sh" article list                 # 文章列表
bash "$S/scripts/run.sh" article detail <id>          # 详情（含正文）
bash "$S/scripts/run.sh" article update <id> --title "新标题" --path new.txt
bash "$S/scripts/run.sh" article delete <id>          # 只列不删
```

**不带 `--yes` 只列不删。** 确认无误后**由用户自己**加 `--yes` 重跑——
不要替他按，也不要引导他"直接加 `--yes` 就行"。

> `article delete` 删的是**该文章的所有版本**，不是只删当前版本。
> `article list` 的 `--template-type` **是必填的**（文档标 false，实测不给报
> `code=100`），默认 `0` 推文。

## 范围之外

- **模板 / 招采文案（ops 51–59）不做**（用户已确认）。
- 用户问到平台别的能力（"能不能做 PPT"、"还能干什么"）→ 让他运行
  `/unipus-aigc:guide`，那里有平台应用的完整清单和可做性分档。

## 更深的材料

这一条是**第二处"整个端点都是空壳"**的样本（第一处是 op50 知识掌握总结），
也是**第一处"文档写了但前端根本没用过"**的样本。接口记录在 plugin 仓库的
`docs/call-chains.md`。
