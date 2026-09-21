---
name: kb-qa
description: >-
  Knowledge-base question answering (RAG) through the Unipus AIGC platform.
  Use when the user wants to ask questions grounded in their own
  documents, wants a document summarised or queried, or mentions 知识库 / RAG /
  "根据这份文档回答". Also covers continuing a line of questioning in one
  conversation (会话 / 多轮追问 / 基于上一个回答 / 接着问), citing the exact chunk an
  answer came from, and rating an answer. Answers come back with citation markers
  and source provenance. 通过 Unipus AIGC 平台做知识库问答，可复用已有知识库，
  也可给若干文档建临时库、答完自动清理。
---

# Unipus AIGC · 知识库问答（RAG）

就用户自己的文档提问，答案带 `[1]` 这样的引用角标和溯源信息。

> **先确认这真的是知识库问答的需求。** 本 skill 只负责"就文档提问"。
> 如果用户其实是在问**平台还能做什么**（"有没有做 PPT 的"、"支持口语评阅吗"、
> "还有哪些应用"），那是 `/unipus-aigc:guide` 的活儿——它有 25 个应用的完整
> 清单和可做性分档。这种情况**不要在这里硬答，也不要说平台没有该功能**。

## 先选链路（这一步不能跳）

这个 skill 里有**两条链路**，共用同一份后端存储（老链路建的库 v2 问得到，
v2 建的库老 `docList` 也读得到），所以**不存在"选错了拿不到数据"**——
只存在"选错了要多绕几步"。判据是**用户的交互形态**，不是文档存在哪里。

| 用户要什么 | 走哪条 | 命令 |
| --- | --- | --- |
| 对一篇（或几篇）文档问一两个问题 | 老链路，**最短路径** | `kb ask "<问题>" --doc /绝对路径/a.txt` |
| 对已有库提问，不需要上下文 | 老链路 | `kb ask "<问题>" --kb KBxxxx` |
| **多轮追问**（后面的问题依赖前面的回答） | **v2** | `kbv2 ask "<问题>" --kb KBxxxx [--session <id>]` |
| 要**指到具体分块**的溯源 | **v2** | `kbv2 ask`（`source_file_info` 给出 `file_id` + `chunk_id` + 命中片段） |
| 给某个回答**认可 / 不认可 / 留反馈** | **v2** | `kbv2 approve <qaId>` / `kbv2 feedback <qaId> "<文字>"` |

对话里的信号词，照这个认：

* "**连着问几个问题**"、"追问"、"接着问"、"**基于上一个回答**"、"记住我们
  聊到哪了" → **v2**，而且要带 `--session`（见"用法 C"）。
* "这份文档里**那个数字**是多少"、"文档里怎么说的" → **老链路** `--doc`，
  一步到位，别为了一个问题去开会话。
* "**这句话出自哪里**"、"我要看到具体段落" → **v2**（分块级溯源）。
* "这个回答**对不对**"、"帮我标一下" → **v2** 的 `approve` / `feedback`。

**老链路没有被淘汰，不要只用 v2。** 老链路是唯一一条**有实测对照实验**证明
能用的通道，回答单条溯源；v2 证据量少于它，但多出会话、问答历史、认可反馈和
分块级溯源。两条并存，按上面的表选。

## 先问清楚

1. **问什么** —— 具体问题。太宽泛（"讲讲这个文档"）也能问，但结果会散。
2. **对哪些文档问** —— 已有的 `kbId`，还是本地文件路径（要绝对路径，可多个）。
3. **要不要保留临时库 / 会话** —— 老链路默认答完删库（`--keep` 保留）；
   v2 默认答完删临时会话（`--keep` 保留）。用户后面还想接着问就别让它删。
4. **会不会有追问** —— 会，就直接走 v2（用法 C），别先用老链路问第一轮。

不知道有哪些库：

```bash
bash "$S/scripts/run.sh" kb list
```

这个命令是**聚合视图**（能看到所有 `source` 的库），看不到某个库时先用它排除
"是不是 `source` 不对"（见下面 `source` 那个坑）。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" kb <子命令> [参数...]       # 老链路
bash "$S/scripts/run.sh" kbv2 <子命令> [参数...]     # v2
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上的 `NotOpenSSLWarning` 是环境噪声，**只看退出码判断成败**。

凭证没配或过期（退出码 1）→ 让用户走 `/unipus-aigc:guide`。

## 用法 A：对已有知识库提问（老链路）

```bash
bash "$S/scripts/run.sh" kb ask "暗号是什么？" --kb KBxxxxxxxx
```

stdout 是答案文本（带 `[1]` 角标），stderr 上有一行 `[溯源]` 给出命中的
文档名和 URL。加 `--json` 输出完整结构。

## 用法 B：给几个文档建临时库（老链路）

```bash
bash "$S/scripts/run.sh" kb ask "这份文档的结论是什么？" --doc /绝对路径/a.txt --doc /绝对路径/b.pdf
```

它会自动：建库 → 上传 → **等解析完成** → 提问 → 删库。
`--keep` 保留库，`--name` 指定库名。

### 退出码 3 时不要重来

文档解析要时间。**超时时 CLI 会保留知识库并退出码 3**，同时在 stderr 打出续跑命令：

```
知识库已保留：KBxxxx
文档解析完成后，用这两条续跑：
  run.sh kb wait KBxxxx --doc <文件名>
  run.sh kb ask "<问题>" --kb KBxxxx
（另有"不再需要时清理"一行，见下面"清理"一节的约束）
```

**照着续跑，不要重新 `--doc` 提交一遍**——那会再建一个库、再传一次文档，
把用户账号堆满垃圾。把 kbId 记下来告诉用户。

CLI 在这段里还会打一行清理命令。**那只是提示"有这么个命令"，不代表可以照着
删**——删不删、什么时候删由用户定，见下面"清理"。

## 用法 C：多轮追问（v2）

**每次 CLI 调用只问一个问题**，所以多轮追问是"同一个 `--session` 反复调"：

```bash
# 第一轮：不给 --session，CLI 建一个临时会话，并把 sessionId 打在 stderr 上
bash "$S/scripts/run.sh" kbv2 ask "这篇报告讲了什么？" --kb KBxxxx --keep

# → stderr 上会有：[会话 3f2a…] 续问：加 --session 3f2a…
# 第二轮：把那个 sessionId 带上，上下文就接上了
bash "$S/scripts/run.sh" kbv2 ask "那它的结论呢？" --kb KBxxxx --session 3f2a…
```

**关键在于把 `--session` 的值记下来并一路带着**。不给 `--session` 时：

* 默认**建一个临时会话、答完就删**——追问就断了；
* 加了 `--keep` 才保留，并把 sessionId 打出来（上面例子用的就是这条）。

不想要上下文（同一个会话里问一个独立问题）加 `--no-history`。

提问成功后 stderr 上还有一行 `[qaId] …`，**`approve` / `feedback` 用的就是它**：

```bash
bash "$S/scripts/run.sh" kbv2 approve <qaId>              # 默认 --value 1 = 认可
bash "$S/scripts/run.sh" kbv2 approve <qaId> --value 2    # 不认可
bash "$S/scripts/run.sh" kbv2 feedback <qaId> "引用的段落不对"
```

v2 自己给几个文件建库（没有 `--doc` 那种一条命令到底的捷径，要分步走）：

```bash
bash "$S/scripts/run.sh" kbv2 create "我的库"              # → 打印 kbId
bash "$S/scripts/run.sh" kbv2 upload <kbId> /绝对路径/a.txt  # 只提交，不等
bash "$S/scripts/run.sh" kbv2 wait <kbId> --doc a.txt        # 等 green + chunkSize>0
bash "$S/scripts/run.sh" kbv2 new-session <kbId>             # → 打印 sessionId
bash "$S/scripts/run.sh" kbv2 ask "问题" --kb <kbId> --session <sessionId>
```

`kbv2 upload --name` 指定文档名时**后缀必须小写**（v2 的硬要求）。

## 最大的坑：解析没完成就提问，会拿到空答案且不报错

文档上传后要等解析。**判定条件是 `status == "green"` 且 `chunkSize > 0`。**

| 字段 | 刚上传 | 解析完成 |
| --- | --- | --- |
| `status` | `"gray"`（v2 多一个中途态 `"purple"`） | `"green"` |
| `ready` | **就已经是 `"green"`** | `"green"` |
| `chunkSize` | `-1` | `> 0` |
| `summary` | `null` | AI 生成的摘要 |

**只看 `status`，绝对不要看 `ready`** —— `ready` 一上传就是 `green`，
拿它判断会在解析完成前就放行。

**v2 多了个 `purple`（解析中），而且 `gray` 和 `purple` 的 `chunkSize` 都是 `-1`**
——所以"`chunkSize > 0`"这个条件在 v2 上也必须一起看，不能只看状态色。
`kbv2 docs <kbId>` 会打出每篇的 `status` 和 `chunkSize`，两个一起看。

解析没完成时提问，接口**返回 200、不报错，但答案是空字符串**（老链路和 v2
都一样）。这是最容易误判的一种失败：看起来成功了，其实什么都没答。
**拿到空答案先怀疑解析，不要当成"知识库里没有这个内容"。**

> 实测补充：这个坑**依赖时序**。一份很小的文档可能在几秒内就解析完，
> 于是"提前提问"也拿到了正确答案——看起来像坑不存在。但事先无法知道解析要多久，
> 所以 `kb wait` / `kbv2 wait` 这道闸门**必须保留**。

手动走一遍（想控制每一步时）：

```bash
bash "$S/scripts/run.sh" kb create "我的库"                       # → 打印 kbId
bash "$S/scripts/run.sh" kb upload <kbId> /绝对路径/a.txt          # 只提交，不等
bash "$S/scripts/run.sh" kb wait <kbId> --doc a.txt --wait 60      # 等 green + chunkSize>0
bash "$S/scripts/run.sh" kb ask "问题" --kb <kbId>
```

`kb wait` / `kbv2 wait` 退出码 3 就是还没解析完，用同一个 kbId 再 wait 一次。

## 坑：`source` 决定库出现在哪个列表里

`kbv2 projects` 列的库**必须带 `--source`**（默认 `1715`），而
**用哪个 `source` 建的库，只会出现在同一个 `source` 的列表里**：

| source | 是什么 | 证据 |
| --- | --- | --- |
| `2` | 前端 / 历史建的库（用户在网页上建的库常常是这个） | 实测建过库、也列表成功过 |
| `1715` | 平台化（基础侧）——默认值 | 实测建过库、也列表成功过 |
| `1720` | 职教 | **仅文档点名，未实测** |
| `default` | 平台的"默认知识库" | 实测（空账号会自动出现一个） |

**只有前两行是实测过的。** `1720` 是接口文档注释里点的名，仓库里没有一条
实测记录——**不要对用户断言"职教库一定在这个 source 下"**，只能说
"这个值要先试"。

所以**用户说"我的库不见了"时，第一个要问的就是"你建库时用的哪个 source"**，
几乎每次都是这个原因，不是库真的丢了。

先让他确认一下（`kb list` 是聚合视图，跨 source 都能看到）：

```bash
bash "$S/scripts/run.sh" kb list                              # 全量，每行带 source
bash "$S/scripts/run.sh" kbv2 projects --source 2             # 只看某一个 source
bash "$S/scripts/run.sh" kbv2 sources                         # 列已实测的 source 取值
```

## 坑：知识库问答的 operation 是 102，**不是**文档枚举里的 16

`kb` 子命令内部用的是 `operation=102`。接口文档的 operation 枚举表里有一条
`16 //知识库问答-新`，很容易顺手改过去——**不要改，16 打不通**：

* 16 的 `submitData` 是 `{question, qaListId, qaId}`，**根本不收 `kbId`**；
* 同一个 `kbId` 下，102 命中知识库内容，16 只回一段固定罐头拒答
  （"抱歉，我无法回答该问题。"），**而且不报错**。

CLI 已经固定在 102，正常用不会碰到这条。只有绕过 CLI 直接照文档调接口时才要
记得这件事。完整对照实验见 plugin 仓库的 `docs/call-chains.md` §3。

## 坑：老链路的参数名极不一致

| 接口 | 正确参数名 | 传错了会报 |
| --- | --- | --- |
| `project/add` | `projectName` | `projectName 不能为空` |
| `project/docList` | **`projectId`** | `知识库Id不能为空` |
| `project/detail` | `projectId` | — |
| `project/docUpload` | **`kbId`** + `docName` + `url` + `type:"document"` | `知识库Id不能为空` / `文档名称不能为空` |
| `project/deleteFile` | `kbId` + `fileId`（值是 docList 里的 `docId`） | `知识库id不能为空` / `文档id不能为空` |
| `project/del` | **`projectIds`（数组）** | `知识库Id不能为空` |

`docList` 用 `projectId`、`docUpload` 用 `kbId` —— **同一个东西两个名字**。
`project/del` 传字符串会报 JSON 解析错误
（`Cannot construct instance of java.util.ArrayList`），必须传数组。

CLI 已经处理好了。只有绕过 CLI 直接读接口时才需要这张表。

## 答案结构

```jsonc
{
  "answer": "这份文档里的暗号是紫色大象在跳舞[1]。",   // [n] 是引用角标
  "ques": "文档里的暗号是什么？",
  "qaId": "2100869059413008386",                      // v2 的 approve/feedback 要的就是它
  "projectId": "KBxxxx",
  "sourceContent": "…命中的原文片段…",                 // 溯源
  "sourceName": "kbtest.txt",
  "sourceUrl": "https://birdflock.unipus.cn/aigc-prod/kb/xxxx.txt",
  "type": "info"
}
```

**汇报给用户时把 `[1]` 对应的出处一起给出**（`sourceName` + `sourceContent`）。
RAG 答案的价值一半在溯源上；只给答案不给出处，用户没法核实。

v2 的返回是 `{qaId, answer}`，溯源在**流式帧**的 `source_file_info` 里
（`file_id` / `chunk_id` / 命中片段），是分块级的。用户在 CLI 上拿到的是答案
文本，原始 JSON 在缓存文件里（见下）。

## 两个副作用要知道

- `kb list` 在账号下一个库都没有时，**平台会自动建一个"默认知识库"**
  （`source == "default"`）。所以第一次 list 之后用户会看到一个不是他建的库，
  这是平台行为。`cleanup` 会跳过它。
- 临时库 / 临时会话如果因为超时或被 `--keep` 保留下来，**它一直在用户账号里
  占着**。任务结束时提醒用户，或者在他确认后清理。

## 清理

```bash
# 老链路：知识库（连带库里的文档）
bash "$S/scripts/run.sh" kb delete <kbId>                        # 只列不删

# v2：知识库 / 文档 / 问答记录
bash "$S/scripts/run.sh" kbv2 delete <kbId>                      # 只列不删
bash "$S/scripts/run.sh" kbv2 rm <kbId> <fileId>                 # 只列不删
bash "$S/scripts/run.sh" kbv2 clear-history <sessionId>          # 只列不删，且**默认清空整个会话**
```

**硬性安全约束：不带 `--yes` 只列不删。任何情况下都不要替用户按 `--yes`，
也不要在话术里引导用户"直接加 `--yes` 就行"。** 要删先把库名 / kbId / 会话 id
和将受影响的内容列给用户看，让他自己确认，再按上面同样的命令加 `--yes`。
`kbv2 clear-history` 不给 `--qa-id` 时删的是**整个会话的问答记录**，不是一条——
列清单时把这一点说清楚。

## 结果的存放位置

每次成功问答都会把原始 JSON 落到 `~/.cache/unipus-aigc/kb-<taskId>.json`
（v2 是 `kbv2-<qaId>.json`），路径打在 stderr 上。

## 更深的材料

调用链、文档解析状态机、v2 与老链路的接口清单见 plugin 仓库的
`docs/call-chains.md` §3。
