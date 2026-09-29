---
name: guide
description: >-
  Entry point for the Unipus AIGC skills. Get the user signed in to the Unipus
  AIGC platform — check whether credentials already exist and renew the token,
  ask for account+password when there are none — then route them to the right
  application skill (翻译 / 作文评阅 / 翻译评阅 / 口语评阅 / 知识库问答 / 语音合成 /
  智能出题 / 图像生成 / 文本生成), or tell them what the platform can and cannot do
  yet. Unipus AIGC 总入口：把用户登录好，再路由到对应的应用 skill。
disable-model-invocation: true
---

# Unipus AIGC · 总入口

先把用户登录好，再把他送到该去的地方。本 skill 只管两件事：**凭证** + **路由**。

## 前提：这是黑盒逆向

平台方没有提供开放 API。这里的调用链是对**自己账号**做黑盒逆向得到的
（前端 webpack 产物 + 抓包验证）。所以：

- 只能让**有正当账号的人用他们自己的凭证**。不要拿别人的 token。
- 接口随时可能变，坏了要重新逆向。
- 不要用它对平台做压测或批量抓取。

平台上共 25 个应用，**还有十几个尚未实现**。用户问到它们时，照内部维护的应用清单
回答（按可做性分档：哪些接口文档已经给全、哪些要重新抓包、哪些不建议做）。
那份清单**不随仓库发布**。**不要对用户谎称某个未实现的应用能用。**

## 命令入口

```bash
# $S = 本 skill 的基目录（加载时会告知 `Base directory for this skill: ...`）
S="<本 skill 基目录>"
bash "$S/skills/guide/scripts/run.sh" <子命令> [参数...]
```

脚本自己会向上找 `.claude-plugin/plugin.json` 定位仓库根、把 `lib/` 挂上
`PYTHONPATH`，**与当前工作目录无关**。

退出码约定（所有应用 skill 通用）：

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 真失败（接口错误 / 任务失败 / 缺凭证） |
| `2` | 用法错误 |
| **`3`** | **仍在处理中——不是错误**。稍后用同一个 id 再 poll 一次 |

stderr 上可能有一条 urllib3 的 `NotOpenSSLWarning`，那是环境噪声，
**判断成败只看退出码，别把 stderr 有输出当失败**。

依赖缺失（`ModuleNotFoundError: requests` / `socketio`）时先装：

```bash
python3 -m pip install -r "$S/requirements.txt"
```

## 第 1 步：检查当前是否有账号密码

**这是本 skill 的第一件事。** 先看落盘里有没有配过账号密码：

```bash
bash "$S/skills/guide/scripts/run.sh" sso status
```

看输出里的 **`密码`** 一行，据此分成两条路：

| `sso status` 的结果 | 含义 | 走哪条 |
| --- | --- | --- |
| `密码      : 已加密落盘` | 配过账号密码 | **有** → 走 A |
| `密码      : (没有)` | 从没配过，或用户 `sso forget` 过 | **没有** → 走 B |
| 命令报 `未找到 JWT` 之类 | 什么都没配 | **没有** → 走 B |

### A. 有账号密码 → 更新 token

**通常什么都不用做**——token 失效时 `load_token()` 会自己续。
但既然用户主动来了，就确认一下、顺手刷一枚新的：

```bash
bash "$S/skills/guide/scripts/run.sh" token            # 退出码 0 = 可用；1 = 过期
bash "$S/skills/guide/scripts/run.sh" sso refresh      # 强制换一枚新的（可选）
```

- 两条都成功 → 告诉用户「凭证正常，JWT 会在过期前自动续」，
  然后直接进第 2 步问他要做什么。
- `sso refresh` 失败（比如 `rt` 过期、密码改过）→ 落到 **B**，重新问他要一次。

### B. 没有账号密码 → 询问用户

**问用户要账号和密码**，然后落到磁盘：

```bash
printf '%s' '<用户给的密码>' | bash "$S/skills/guide/scripts/run.sh" sso login --account '<邮箱>' --stdin
```

配一次之后 JWT 每 48 小时自动换新，`rt` 30 天过期后自动用密码重登。
**以后不用再管凭证。**

用户当场不想给账号密码，就告诉他还有**手动粘 JWT**那条路
（`set-token --stdin`，做法见 `$S/skills/guide/SKILL.md`），
或者干脆先跳过——下次要用应用时再配。

> **JWT / 密码等于账号密码，不要提交到仓库、不要转发给任何人。**
> 回显只说写入路径、账号、JWT 指纹、有效期，**绝不复述密码或任何 token**。
> 手动粘 JWT 的取值步骤、密码加密的边界（密钥默认与 `.env` 同目录，
> 挡得住误提交、挡不住能读 home 的进程），都在 `$S/skills/guide/SKILL.md` 里。

### 凭证读取优先级（先到先得）

1. 环境变量 `UNIPUS_AIGC_TOKEN`
2. `./.env`（当前工作目录）
3. `~/.config/unipus-aigc/.env` ← `sso login` / `set-token` 默认写这里

## 第 2 步：路由到应用 skill

凭证没问题之后，按用户想做的事，**读对应的文件、照它做**：

| 用户想做什么 | 读哪个文件 |
| --- | --- |
| 翻译一段文字，或翻译一个文档（docx/pdf 等） | `$S/skills/translate/SKILL.md` |
| 给英语作文打分、要逐句纠错和改写建议 | `$S/skills/review/SKILL.md` |
| **给一份译文打分**（只出分数，**不产出译文**） | `$S/skills/trans-review/SKILL.md` |
| 给一段**朗读音频**打分、要发音反馈 | `$S/skills/oral-review/SKILL.md` |
| 就自己的文档提问（RAG）、要带出处的答案 | `$S/skills/kb-qa/SKILL.md` |
| 把一段文字念成音频（TTS / 朗读 / 配音 / 听力音频） | `$S/skills/speech/SKILL.md` |
| **根据阅读材料出题**（单选/多选/判断/问答，可采纳） | `$S/skills/question-gen/SKILL.md` |
| **画一张图**（AI 绘画 / 文生图 / 给文章配图） | `$S/skills/image-gen/SKILL.md` |
| **写文章**（起标题 / 列大纲 / 续写 / 改写） | `$S/skills/text-gen/SKILL.md` |

这些应用 skill 是**跟着本目录一起走的文件**，不靠任何注册机制：读进来照做即可。
每个应用的文件里有它自己的参数引导和坑位清单，**不要**照着本文件猜平台命令。

> 读的时候注意：那些文件里的 `$S` 指的是**它们自己的目录**（也就是
> `$S/skills/<名字>/`），所以它们的命令入口是 `$S/skills/<名字>/scripts/run.sh`。

> **别把 `translate` 和 `trans-review` 搞混**：前者**产出译文**，后者**只给译文打分**。
> 用户说"帮我改改这段译文"走 `translate`；说"这段翻得怎么样"走 `trans-review`。
> 这条名字在早期版本里叫"译后编辑"，是**错的**。

## 记录管理

这几个命令属于本 skill，不属于任何应用 skill。

```bash
bash "$S/skills/guide/scripts/run.sh" records                       # 列出翻译/评阅/知识库的历史记录
bash "$S/skills/guide/scripts/run.sh" cleanup                       # 只列出可清理的记录，不删
bash "$S/skills/guide/scripts/run.sh" cleanup --pattern <关键字>     # 按名称模糊匹配，仍只列不删
bash "$S/skills/guide/scripts/run.sh" cleanup --ids <id> --ids <id> --yes   # 精确删除
```

**硬性安全约束：不带 `--yes` 就只列不删。任何情况下都不要替用户按 `--yes`，
也不要在话术里引导用户"直接加 --yes 就行"。** 要删就先把列表给用户看，
让他自己确认哪些 id，再用 `--ids` 精确指定（比 `--pattern` 模糊匹配安全）。

两个已知限制，用户问起时要说：

- `translate/list` **不返回纯文本翻译记录**（`type=1`），所以文本翻译产生的
  记录 `cleanup` 看不见，会静默累积。
- `cleanup` 会跳过平台自带的"默认知识库"（`source == "default"`）。

## 出错了怎么排查

| 症状 | 先看什么 |
| --- | --- |
| `未找到 JWT` | 没配过凭证 → 走「第 1 步」的 B（问用户要账号密码） |
| `EXPIRED` | 配过的话先 `sso refresh`；还不行就走 B 重新给一次 |
| 平台返回 `401` / 「用户登录失效」 | 同上——**这是凭证问题，不是参数问题** |
| 退出码 `3` | **不是错误**，任务还在跑，稍后再 poll |
| `文档翻译失败` | 大概率是语种码问题，见 `translate` skill |
| `socketId不能为空` | socket.io 没连上，检查 `python-socketio`/`websocket-client` 装没装 |

> ⚠️ **`sso refresh` 报「会话已失效」之类的错**：`sso refresh` 需要一份
> **有效的 rt**。`sso status` 里如果显示 `rt` 已经过期、或者有 rt 但仍然
> 续不上，就**别在 refresh 上反复试**——直接走 B 重新登录一次。
