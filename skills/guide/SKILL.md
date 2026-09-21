---
name: guide
description: >-
  Entry point for the Unipus AIGC plugin. Configure the login credential (JWT),
  check token validity, list or clean up platform records, and route the user to
  the right application skill (translate / review / kb-qa) or tell them what the
  platform can and cannot do yet. Unipus AIGC 引导入口：配置登录凭证、体检 token、
  查看与清理历史记录、按需求路由到应用 skill。
disable-model-invocation: true
---

# Unipus AIGC · 引导

这个 plugin 把 Unipus AIGC 平台的调用链封装成了 skills。
本 skill 是**总入口**，管三件事：**配凭证**、**体检**、**路由到应用 skill**。

本 skill 只能由用户主动唤起（`/unipus-aigc:guide`），模型不会自触发它。

## 前提：这是黑盒逆向

平台方没有提供开放 API。这里的调用链是对自己账号做黑盒逆向得到的
（前端 webpack 产物 + 抓包验证）。所以：

- 只能让**有正当账号的人用他们自己的凭证**。不要拿别人的 token。
- 接口随时可能变，坏了要重新逆向。
- 不要用它对平台做压测或批量抓取。

跟用户说明能力边界时把这一条讲清楚。

## 命令入口

所有命令都走本 skill 目录下的壳脚本。把 `$S` 换成**本 skill 的基目录**
（加载本 skill 时系统会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" <子命令> [参数...]
```

脚本自己会向上找 `.claude-plugin/plugin.json` 定位 plugin 根、把 `lib/` 挂上
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

## 第 1 步：体检

```bash
bash "$S/scripts/run.sh" token
```

- 退出码 `0` → 凭证有效，输出里带剩余天数。
- 退出码 `1` 且提示 `EXPIRED` → 过期了，走下面「配凭证」。
- 退出码 `1` 且提示 `未找到 JWT` → 没配过，走「配凭证」。

依赖缺失（`ModuleNotFoundError: requests` / `socketio`）时先装：

```bash
python3 -m pip install -r "<plugin 根>/requirements.txt"
```

## 第 2 步：配凭证

JWT 是用户**自己**的登录凭证，**等于账号密码**。

让用户自己去拿（不要替他猜、不要用任何别人的凭证）：

1. 浏览器登录 <https://ai.unipus.cn>
2. 开发者工具 → Application/存储 → Local Storage
3. 找 `userInfo` 这一项，复制里面 `jwt` 字段的值

用户把 JWT 告诉你之后，落盘：

```bash
printf '%s' '<用户给的 JWT>' | bash "$S/scripts/run.sh" set-token --stdin
```

用 `--stdin` 而不是位置参数，这样 token 不会出现在 `ps` 输出里。
默认写到 `~/.config/unipus-aigc/.env`（权限 `600`，目录 `700`）；
加 `--scope cwd` 则写当前目录的 `.env`。

**回显给用户时只说三件事：写入路径、指纹、有效期。绝不复述 JWT 本身。**
覆盖旧值时会自动备份成 `<path>.bak`（同样 `600`），传错了能恢复。

必须对用户说清楚的一句话：

> **JWT 等于账号密码，不要提交到仓库、不要转发给任何人。**

凭证读取优先级（先到先得）：

1. 环境变量 `UNIPUS_AIGC_TOKEN`
2. `./.env`（当前工作目录）
3. `~/.config/unipus-aigc/.env` ← `set-token` 默认写这里

## 第 3 步：路由到应用 skill

已跑通、可直接用的六个：

| 用户想做什么 | 唤起哪个 skill |
| --- | --- |
| 翻译一段文字，或翻译一个文档（docx/pdf 等） | `unipus-aigc:translate` |
| 给英语作文打分、要逐句纠错和改写建议 | `unipus-aigc:review` |
| **给一份译文打分**（只出分数，**不产出译文**） | `unipus-aigc:trans-review` |
| 给一段**朗读音频**打分、要发音反馈 | `unipus-aigc:oral-review` |
| 就自己的文档提问（RAG）、要带出处的答案 | `unipus-aigc:kb-qa` |
| 把一段文字念成音频（TTS / 朗读 / 配音 / 听力音频） | `unipus-aigc:speech` |

> **别把 `translate` 和 `trans-review` 搞混**：前者**产出译文**，后者**只给译文打分**。
> 用户说"帮我改改这段译文"走 `translate`；说"这段翻得怎么样"走 `trans-review`。
> 这条名字在本仓库早期叫"译后编辑"，是**错的**，详见
> [references/app-routing.md](references/app-routing.md) 的说明。

**用 Skill 工具去唤起**，不要自己照着文档拼命令——应用 skill 里有各自的
坑位清单和参数引导。

平台上总共 25 个应用，**还有十几个尚未实现**。用户问到它们时，读
[references/app-routing.md](references/app-routing.md)：那里按可做性分了档，
告诉你哪些"接口文档已经给全、照着骨架就能补"、哪些"要重新抓包"、哪些"不建议做"。
**不要对用户谎称某个未实现的应用能用。**

## 记录管理

这三个命令属于本 skill 的域，不属于任何应用 skill。

```bash
bash "$S/scripts/run.sh" records                       # 列出翻译/评阅/知识库的历史记录
bash "$S/scripts/run.sh" cleanup                       # 只列出可清理的记录，不删
bash "$S/scripts/run.sh" cleanup --pattern <关键字>     # 按名称模糊匹配，仍只列不删
bash "$S/scripts/run.sh" cleanup --ids <id> --ids <id> --yes   # 精确删除
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
| `未找到 JWT` | 三处凭证位置都没配，走「配凭证」 |
| `EXPIRED` | token 过期，重新登录复制 |
| 退出码 `3` | **不是错误**，任务还在跑，稍后再 poll |
| `文档翻译失败` | 大概率是语种码问题，见 translate skill |
| `socketId不能为空` | socket.io 没连上，检查 `python-socketio`/`websocket-client` 装没装 |

完整的接口级排查材料在 plugin 仓库的 `docs/call-chains.md`（逐条调用链 +
字段名踩坑表）和 `docs/app-catalog.md`（25 个应用全景）。
