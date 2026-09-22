---
name: task-gen
description: >-
  Generate teaching tasks (exercise / task cards) through the Unipus AIGC
  platform — a task is an ordered chain of steps across several apps, e.g.
  TTS then oral review, an essay prompt then essay review, a reading passage
  then question generation. Supports batch generation with non-repeating
  content, and an optional runner that executes one task end to end. Use when
  the user asks for 生成任务 / 造任务 / 出一批练习 / 批量出题 / 任务链 /
  教学任务 / 组卷 / 阅读练习 / 写作练习 / 翻译练习 / 朗读任务 / exercise /
  task card. 生成教学任务：把多个应用编排成有序任务链，默认只出任务卡、
  不调用平台。
---

# Unipus AIGC · 任务生成

把平台上**已经跑通的九个应用**编排成「像真题一样」的**有序任务**。

**一条任务 = 若干步**，每步写明用哪个应用、参数是什么、上一步的什么产出喂给
下一步。比如：

```
朗读 → 口语评阅     先合成一段标准范读音频，再交口语评阅打分
作文题目 → 作文评阅  先出题，学生写完交作文评阅
阅读材料 → 出题 → 答题   给材料、出题；答题由学生自己做
翻译 → 翻译评阅     出参考译文，学生翻一遍，系统打分
```

**批量生成**时保证内容不重复（同一个 `--seed` 还能完全复现）。

## 先问清楚两件事

1. **要哪种场景**（上面四条里挑，或 `--chain all` 全要）。
2. **要几条**（`--count` 是**每条链路**各几条；`--chain all --count 5` = 20 条）。

## 凭证（token）

**先直接跑，别先问凭证。** `gen` / `chains` / `show` / `check` / `list`
这几条**一个平台请求都不发**，没配凭证也能用。续期也是自动的——只要之前
配过账号密码，token 失效时会自己换新。

只有**真跑**（`run --go`）或者 `materials` **真的失败**之后才需要处理。
先看一眼现状：

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

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" exercise <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错（本地校验没过，**一个请求都没发**）/
**`3` 仍在处理中（不是错误）**。stderr 上的 `NotOpenSSLWarning` 是环境噪声。

## ⭐ 最重要的一条：`gen` 不碰平台

```bash
bash "$S/scripts/run.sh" exercise chains            # 看四条链路和各自的步骤
bash "$S/scripts/run.sh" exercise gen --chain all --count 5 --seed 42
```

`gen` **只读本地素材库、只写本地文件**，一条网络请求都不发。任务卡落在
`~/.cache/unipus-aigc/exercise/<批次号>/`：

```
manifest.json                    批次元信息（条数、seed、每条内容的指纹）
tasks/001-EX-…-001.json          机器可读
tasks/001-EX-…-001.md            人类可读（可以直接发给学生）
```

**这是刻意的**：生成 100 条任务和提交 100 个任务是两件完全不同的事，
不该用同一个命令加个开关来区分。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--chain <名\|all>` | 链路名，默认 `all`；多个用逗号分隔 |
| `--count N` | **每条链路**各 N 条 |
| `--seed N` | 给了就完全可复现（同 seed 两次跑，任务卡逐字节相同） |
| `--rm-id <id>` | 只有「阅读材料 → 出题」链路需要（见下） |
| `--out <目录>` | 换落盘根目录 |
| `--print` | 同时把任务卡打到 stdout |

其它子命令：

```bash
bash "$S/scripts/run.sh" exercise list                     # 已有的批次
bash "$S/scripts/run.sh" exercise show <taskId>            # 打印一条任务卡
bash "$S/scripts/run.sh" exercise show <taskId> --student   # 学生版（隐藏参考译文）
bash "$S/scripts/run.sh" exercise check <batchId|taskId>    # 逐条校验，全离线
bash "$S/scripts/run.sh" exercise materials                 # 可用的阅读材料（只读，要 token）
```

`--count` 超过素材数会**直接报错**（退出码 2），不会悄悄给你一批重复的。
`check` 会验：内容不重复、占位符都解析了、参数落在各应用的白名单里。

## 真跑一条

```bash
bash "$S/scripts/run.sh" exercise run <taskId>              # **只打印计划**，一个请求都不发
bash "$S/scripts/run.sh" exercise run <taskId> --go         # 真的执行
```

**`--go` 是独立的动作，不是 `gen` 上的开关。** 不给 `--go` 时连凭证都不需要，
所以计划随时可以看。

| 开关 | 作用 |
| --- | --- |
| `--go` | 真的执行。不给就只打印"将要提交 N 个任务、涉及哪些应用、预计残留" |
| `--auto` | **人工步骤**（学生写作文 / 翻译 / 答题）用预制替身顶上，只用于演示整条链 |
| `--all-steps` | 可选步骤也跑（目前只有「建阅读材料」一个是可选的） |

**跑完会打一份残留清单**：平台上留下了哪几条记录、id 是多少、用什么命令清。

> **执行器自己绝不删任何东西，也绝不代按 `--yes`。** 清理要用户自己确认。
> 你也一样：不要替他按，也不要说"直接加 `--yes` 就行"。

跑了一半中断（超时 / Ctrl-C）不要紧：进度写在任务卡旁边，
**再跑一次会跳过已经完成的步骤，不重复提交**。

## 四条链路各自的注意事项

### 朗读 → 口语评阅

先 TTS 合成一段标准朗读，再交口语评阅打分。第 2 步直接吃第 1 步的音频地址，
不重新上传。

> ⚠️ **TTS 合成的是标准发音，评阅分数基本都偏高（实测 96–98）。**
> 这条任务的价值在「给学生一段范读」和「看 feedback 里该怎么读」，
> **不在「测出学生多差」**。跟用户说明时别把它说成测评。

### 作文题目 → 作文评阅

> ⚠️ **第一步生成的是「题目」，不是范文。** 学生要自己写。
> 平台的写作能力产出的文章拿去评阅只会拿高分，学生学不到东西。
> **不要**把它做成一键出范文——那会让整条链路失去意义。

### 阅读材料 → 出题 → 答题

> ⚠️ **两件事必须说清楚：**
>
> 1. **平台没有 `rm/delete`——阅读材料建了就删不掉。** 所以默认路线是
>    复用**已有**的材料 + `preview` 出题（同步回题面、**不落库、零残留**）。
>    "建材料"那一步是**可选**的，默认不跑。
> 2. **「答题」那一步没有可用的接口。** 文档里的 `ques/ans` 实测在两个主机上
>    都回 404，前端产物里也搜不到它——所以它是**人工步骤**，平台不判分。
>
> 这条链路要先绑材料：`exercise materials` 拿一个 `rmId`，
> 再用 `gen --rm-id <id>` 重新生成。没绑的任务卡 `check` 会标出来，
> `run --go` 会直接拒绝（退出码 2）。

### 翻译 → 翻译评阅

> ⚠️ **翻译评阅（op36）只打分，不产出译文。** 返回的 `translation` 是你提交
> 的那份的**原样存档**，别读成"平台改过的"。想要"改后的译文"这个接口给不了。
>
> 语种码只认小写 `en`/`zh`，写错**一次都不报错**、只给个假分数——
> CLI 在本地就挡住了（退出码 2）。
>
> `--auto` 会**拿参考译文当作学生的译文**交上去，那只是把链路演示完，
> **不是「替学生翻了一遍」**。

## 范围之外

- 本 skill **不由 `guide` 路由**——用户直接 `/unipus-aigc:task-gen` 唤起。
- 想用别的应用（翻译一段文字、画一张图、就文档提问……）→ 让他运行
  `/unipus-aigc:guide`，那里有平台应用的完整清单和可做性分档。
- 本 skill **不给任务判分**：任务卡里写的是"期望产出"，学生交上来的东西
  对不对归评阅类应用管。

## 更深的材料

四条链路的接口依据、每个应用的坑位清单、以及"`ques/ans` 打不通"这条实测，
都在 plugin 仓库的 `docs/call-chains.md`（§12 是任务编排）。
