# auto-aigc-tasks

`ai.unipus.cn`（Unipus AIGC 平台）的 **Claude Code plugin**。

把平台上九个已跑通的 AIGC 应用——**文档/文本翻译、智能评阅（作文）、翻译评阅、
口语评阅、知识库问答（RAG）、语音合成（TTS）、智能出题、图像生成、文本生成**——
封装成 skills，让 Claude 能直接引导用户完成调用；底下是一个 Python 客户端
（`lib/unipus_aigc/`），**skill 是壳，Python 是手**。

- 完整调用链记录：[docs/call-chains.md](docs/call-chains.md)
- 平台上全部应用的可做性分档：[docs/app-catalog.md](docs/app-catalog.md)

---

## 五分钟上手

```bash
# 1) 装依赖（plugin 不自带）
python3 -m pip install -r requirements.txt

# 2) 配凭证：给一次账号密码，之后 JWT 自动续期
printf '<你的密码>' | bash skills/guide/scripts/run.sh sso login --account <邮箱> --stdin

# 3) 体检
bash skills/guide/scripts/run.sh token       # 退出码 0 = 可用

# 4) 跑一条
bash skills/translate/scripts/run.sh translate text "Hello, world."
```

在 Claude Code 里则是 `/unipus-aigc:guide` —— 它带你配凭证、体检、路由到应用 skill。

---

## 两个入口，挑一个

| | 怎么用 | 适合 |
| --- | --- | --- |
| **对话** | `/unipus-aigc:guide`，或直接说"帮我翻译这段" | 不想记命令；让 Claude 引导 |
| **命令行** | `bash skills/<app>/scripts/run.sh <域> <子命令>` | 脚本、CI、可复现 |

两条路走的是**同一套代码**。每个 skill 目录下的 `run.sh` 是自定位的壳脚本
（向上找 `.claude-plugin/plugin.json` 定位 plugin 根、把 `lib/` 挂上 `PYTHONPATH`），
**与当前工作目录无关**。也可以绕过壳脚本：

```bash
PYTHONPATH=lib python3 -m unipus_aigc.cli <子命令> ...
```

**退出码**（所有应用通用）：

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 真失败（接口错误 / 任务失败 / 缺凭证） |
| `2` | 用法错误（本地校验拦下的） |
| **`3`** | **仍在处理中——不是错误**。状态在平台侧，稍后用同一个 id 再跑一次 |

stderr 上可能有一条 urllib3 的 `NotOpenSSLWarning`，那是环境噪声
（macOS 的 LibreSSL），**判断成败只看退出码**。

---

## 配置凭证

两种方式，**推荐第一种**。

### 方式一：账号密码，之后自动续期（推荐）

```bash
S=skills/guide/scripts/run.sh
printf '<你的密码>' | bash $S sso login --account <邮箱> --stdin
```

密码**从 stdin 读**（避免进 `ps` 和 shell 历史）。配一次之后：

* JWT 每 **48 小时**自动换新（用 `rt` 换，不用密码）；
* `rt` **30 天**过期后，用落盘的密码自动重登；
* 全程无感——`load_token()` 里按需续（到期前 5 分钟），**还早的话不打网络**。

```bash
bash $S sso status      # 看材料齐不齐、JWT/rt 各还剩多久
bash $S sso refresh     # 强制续一次（平时不用手动跑）
bash $S sso forget      # 反悔：删掉密码和 rt，只留 JWT（不带 --yes 只列不删）
```

> ⚠️ **密码会加密落盘**（`UNIPUS_AIGC_PASSWORD_ENC`，AES-128-CBC + HMAC-SHA256）。
> 但**密钥默认和 `.env` 在同一个目录**（`~/.config/unipus-aigc/secret`），
> 所以这层加密挡的是"`.env` 被单独备份 / 分享 / 误提交"，
> **挡不住**能读你 home 目录的进程。想真隔开就把 `UNIPUS_AIGC_SECRET`
> 放进环境变量（比如从系统钥匙串注入）。
> 详见 [docs/call-chains.md](docs/call-chains.md) §11。

### 方式二：手动粘一枚 JWT

登录 <https://ai.unipus.cn> → 开发者工具 → Local Storage → `userInfo` 里的 `jwt`。

```bash
printf '%s' '<JWT>' | bash $S set-token --stdin
```

回显只有路径、sha256 指纹和有效期，**不复述 token 本身**。
代价是 48 小时后要再粘一次。

> **JWT 等于账号密码，不要提交到仓库、不要转发给任何人。**

### 凭证读取优先级（先到先得）

1. 环境变量 `UNIPUS_AIGC_TOKEN`
2. `./.env`（当前工作目录）
3. `~/.config/unipus-aigc/.env` ← `sso login` / `set-token` 默认写这里，`600`

第 3 条是装成 plugin 后的常态路径——**plugin 场景下 CWD 不再是仓库根**。

> ⚠️ 第 2 条排在前面意味着**仓库根那份 `.env` 会盖住用户级文件**。
> `sso login` 已处理：新 JWT 会同步到其它也含 `UNIPUS_AIGC_TOKEN` 的文件
> （**只同步 token 那一行**，密码/rt 不进仓库工作区）。

---

## 九个应用怎么用

`$S` = 对应 skill 的 `scripts/run.sh`。所有命令都可以在 Claude Code 里让
`/unipus-aigc:guide` 帮你拼。

### 1. 翻译 —— `skills/translate/scripts/run.sh`

```bash
S=skills/translate/scripts/run.sh

# 文本翻译（秒级）
bash $S translate text "The quick brown fox jumps over the lazy dog." --to zh

# 文档翻译：必须拆两步（前台命令有 600s 上限）
bash $S translate submit-doc ./paper.pdf --to zh        # 秒级返回记录 id
bash $S translate poll <记录id> --out ./paper.zh.docx   # 可反复调用；退出码 3 = 还在跑
```

**文档翻译只吃两字母语种码**（`en`/`zh`），三字母（`eng`/`zho`）立刻失败。

### 2. 作文评阅 —— `skills/review/scripts/run.sh`

```bash
bash skills/review/scripts/run.sh review essay --path essay.txt \
     --topic "AI and Study" --level 0     # 0 大学 / 1 高中 / 2 初中 / 3 小学
```

回总分 + 四个分项 + 逐句纠错。**结果走 `task/queryTask`，不走 `wm/detail`**（后者
`evaluation` 恒为 null）。

### 3. 翻译评阅 —— `skills/trans-review/scripts/run.sh`（别名 `tr`）

```bash
bash skills/trans-review/scripts/run.sh tr review \
     --src-text "The library opens at eight." \
     --tgt-text "图书馆早上八点开门。"
```

**只打分，不产出译文。** 语种码写错**不报错、只给假分数**，所以 CLI 在本地就挡。
要译文走 `translate`。

### 4. 口语评阅 —— `skills/oral-review/scripts/run.sh`

```bash
bash skills/oral-review/scripts/run.sh oral review ./a.mp3 --content "I went to school."
```

回 `evaluation.overall` 百分制 + 逐词发音反馈。

### 5. 知识库问答 —— `skills/kb-qa/scripts/run.sh`

```bash
S=skills/kb-qa/scripts/run.sh

# 临时建库、传文档、提问、答完自动删库
bash $S kb ask "文档里的暗号是什么？" --doc ./doc.txt

# 也可以对已有知识库提问
bash $S kb ask "暗号是什么？" --kb KBxxxxxxxx

# 多轮追问 / 分块级溯源走 v2（同一份存储，按交互形态选）
bash $S kbv2 ask "接着问" --kb KBxxxxxxxx --session <id>
```

### 6. 语音合成 —— `skills/speech/scripts/run.sh`

```bash
S=skills/speech/scripts/run.sh
bash $S speech speakers                                        # 五个实测白名单音色
bash $S speech say "要念的文字" --speaker zh_youyou --out ./a.mp3
```

### 7. 智能出题 —— `skills/question-gen/scripts/run.sh`

```bash
S=skills/question-gen/scripts/run.sh
bash $S questions ploys                                             # 策略表
bash $S questions materials                                         # 已有材料（含 generateCount）
bash $S questions preview <rmId> --ploy 1011:2                      # 试策略，**不留残留**
bash $S questions generate <rmId> --ploy 1010:1                     # 真出题，回 quesId
bash $S questions accepted <rmId>                                   # 已采纳
bash $S questions accept <quesId> / accept <quesId> --cancel        # 采纳 / 取消
bash $S questions json <rmId>                                       # 原文 + 题
```

⚠️ **阅读材料建了删不掉**（`rm/delete` 不存在）——能复用就复用。

### 8. 图像生成 —— `skills/image-gen/scripts/run.sh`（别名 `img`）

```bash
S=skills/image-gen/scripts/run.sh
bash $S image styles                                                 # 白名单 + 每个风格的尺寸表
bash $S image sizes general_v2.1_L                                   # 某风格的合法尺寸
bash $S image draw "一只打盹的橘猫" --style general_v2.1_L --size 正方形
bash $S image records                                                # 记录（删记录用这行的 id）
bash $S image delete <id>                                            # 不带 --yes 只列不删
```

⚠️ **风格表要现取**——白名单里有不等于能出图，只有 `general_v2.1_L` 实测出图。

### 9. 文本生成 / 文章写作 —— `skills/text-gen/scripts/run.sh`

```bash
S=skills/text-gen/scripts/run.sh
bash $S article create --title "AI 与教育"                # -> articleId
bash $S article title --article-id <id> -t 1             # 10 条标题（-t 1/2/3）
bash $S article outline <id> --path a.txt                # markdown 大纲（非流式）
bash $S article continue <id> --start "…" --end "…"      # 续写（SSE，默认拼好再打）
bash $S article rewrite <id> --content "…" --method 1    # 改写（SSE）
bash $S article delete <id>                              # 能删干净
```

### 其它域

```bash
S=skills/guide/scripts/run.sh
bash $S records                     # 列各类历史记录
bash $S cleanup --ids <id>          # 清理测试数据（不带 --yes 只列不删）
bash $S sync ops                    # 同步 operation 清单（11/13/14/15/17）
bash $S sync cs-qa "问题" --standard-id 0
```

---

## 完整命令表

| 域 | 子命令 | 一句话 |
| --- | --- | --- |
| `translate` | `text` / `submit-doc` / `poll` / `get` | 文本翻译、文档翻译 |
| `review` | `essay` / `submit` / `poll` / `get` | 作文评阅 |
| `trans-review`（`tr`） | `review` / `submit` / `poll` / `get` / `detail` / `records` / `delete` | 翻译评阅 |
| `oral` | `review` / `submit` / `poll` / `get` / `detail` / `records` / `delete` | 口语评阅 |
| `kb` | `list` / `ask` / `create` / `upload` / `wait` / `delete` / `submit-question` / `poll` | 知识库问答 |
| `kbv2` | `projects` / `sources` / `create` / `upload` / `wait` / `docs` / `ask` / `sessions` / `session` / `history` / `new-session` / `rename` / `rm` / `delete` / `clear-history` / `approve` / `feedback` / `prompt` | RAG v2（会话 / 分块级溯源） |
| `speech` | `speakers` / `say` / `submit` / `poll` / `get` / `records` | 语音合成 |
| `questions` | `ploys` / `materials` / `material` / `create-material` / `update-material` / `preview` / `generate` / `records` / `record` / `accepted` / `accept` / `json` / `answer` / `delete` | 智能出题 |
| `image`（`img`） | `styles` / `sizes` / `draw` / `submit` / `poll` / `get` / `records` / `delete` | 图像生成 |
| `article`（`art`） | `create` / `detail` / `list` / `update` / `delete` / `title` / `outline` / `continue` / `common-continue` / `rewrite` | 文本生成 |
| `sync` | `ops` / `standards` / `cs-qa` / `prompt-optimize` / `prompt-translate` / `kb-view` / `question-answer` / `submit` / `poll` | 同步 operation |
| `sso` | `login` / `refresh` / `status` / `forget` | 凭证自动续期 |
| — | `token` / `set-token` / `records` / `cleanup` | 凭证与记录管理 |

---

## 作为库使用

```python
from unipus_aigc import UnipusAIGC, ReviewAPI

with UnipusAIGC() as cli:                       # 自动管理 socket.io 连接 + 凭证续期
    # 1. 文本翻译
    r = cli.translate.text("The quick brown fox jumps over the lazy dog.", "en", "zh")
    print(cli.translate.translated_text(r))

    # 2. 文档翻译：长任务用 submit + poll，不要阻塞等
    rec = cli.translate.submit_document("./a.pdf", "en", "zh")   # 秒级返回
    detail = cli.translate.poll(rec["id"], timeout=60)           # 未完成抛 StillRunning
    print(detail["translateUrl"])

    # 3. 作文评阅
    report = cli.review.essay("Many student uses AI tools to help they finish homework.",
                              topic="AI and Study", level=0)
    print(ReviewAPI.format_report(report))

    # 4. 知识库问答
    kb_id = cli.kb.create("我的知识库")
    cli.kb.upload_document(kb_id, "./doc.txt")          # 等到解析完成才返回
    answer = cli.kb.ask(kb_id, "文档讲了什么？")
    print(cli.kb.answer_text(answer), answer["sourceUrl"])

    # 5. 图像生成（风格表现取）
    cli.image_gen.styles()                              # -> ['general_v2.1_L', ...]
    print(cli.image_gen.draw_and_wait("一只打盹的橘猫"))

    # 6. 文本生成（后三个是 SSE 流式）
    aid = cli.article.create(title="AI 与教育")
    print(cli.article.ai_title(1, article_id=aid))      # 10 条标题
    print(cli.article.outline(aid, "人工智能正在改变教育。"))
```

单独用凭证那层（不用建 client）：

```python
from unipus_aigc import config, sso

config.load_token()                       # 取一枚有效 JWT（按需自动续期）
config.save_credentials(jwt, rt, ...)     # 落盘一整套
sso.login(account, password)              # 走一次 SSO 三步登录
sso.refresh(rt)                           # 用 rt 换新 JWT
```

---
## 授权与合规（先读这段）

**平台方没有提供开放 API。** 这里的调用链是对**自己账号**的接口整理
（以内部接口文档为准，文档没覆盖处以黑盒逆向补齐：前端 webpack 产物 + 抓包验证）。
只覆盖登录后**本就能用**的功能。因此：

- 只适合**在平台上有正当账号的人，用他们自己的凭证**使用。
- 不要拿别人的 token / 账号，不要用它对平台做压测或批量抓取。
- 接口随时可能变；坏了就要重新逆向或重新对文档。
- 本仓库与 Unipus / 外研社无任何隶属或授权关系。

## 安装

### 作为 plugin 使用

```bash
claude plugin marketplace add <本仓库路径或 git 地址>
claude plugin install unipus-aigc@<marketplace 名>
```

装完**重启** Claude Code 才生效——已安装的 plugin 是按版本号快照复制的，不是活的软链。

### 开发期

```bash
claude --plugin-dir .        # 会话级加载，直接读目录，绕过缓存
```

改完 skill 重开一个会话即可，不用走 install / update。

### Python 依赖

plugin **不**自带依赖，要单独装：

```bash
python3 -m pip install -r requirements.txt
```

依赖：`requests`、`python-socketio`、`websocket-client`。
代码兼容 **Python 3.9+**（不要用 3.10+ 语法）。
**加密那块是纯标准库实现**，没有额外依赖。

### 怎么选到哪个 skill

| 你想要的 | 用哪个 skill |
| --- | --- |
| 翻译文本 / 文档 | `unipus-aigc:translate` |
| 给英语作文打分、逐句纠错 | `unipus-aigc:review` |
| **给一份译文打分**（只出分，不产出译文） | `unipus-aigc:trans-review` |
| 给朗读音频打分、要发音反馈 | `unipus-aigc:oral-review` |
| 就自己的文档提问、要带出处的答案 | `unipus-aigc:kb-qa` |
| 把文字念成音频（TTS） | `unipus-aigc:speech` |
| 根据阅读材料出题、可采纳 | `unipus-aigc:question-gen` |
| 画一张图 / 给文章配图 | `unipus-aigc:image-gen` |
| 写文章（起标题 / 列大纲 / 续写 / 改写） | `unipus-aigc:text-gen` |
| 配凭证、体检、清理、问"平台还能干什么" | `unipus-aigc:guide` |

`guide` 带 `disable-model-invocation: true`，**模型看不见它、也无法代你触发**。
这是有意的：它管凭证，不该在无关对话里被自动唤起。代价是"这个平台还能干什么"
这类问题模型会错配到某个应用 skill——每个应用 skill 里都写了兜底提示，
会把用户转回 `/unipus-aigc:guide`。

## 结构

```
.claude-plugin/plugin.json   plugin 清单
skills/
├── guide/       SKILL.md + scripts/run.sh + references/app-routing.md
├── translate/   SKILL.md + scripts/run.sh
├── review/      SKILL.md + scripts/run.sh
├── trans-review/ SKILL.md + scripts/run.sh
├── oral-review/ SKILL.md + scripts/run.sh
├── kb-qa/       SKILL.md + scripts/run.sh
├── speech/      SKILL.md + scripts/run.sh
├── question-gen/ SKILL.md + scripts/run.sh
├── image-gen/   SKILL.md + scripts/run.sh
└── text-gen/    SKILL.md + scripts/run.sh
lib/unipus_aigc/
├── config.py      主机地址、凭证读取与写入、JWT 解析、按需续期
├── client.py      UnipusAIGC：请求头 + POST + 七牛上传 + socketId + 任务轮询
├── constants.py   操作码、状态码、学段、两套语种码
├── sso.py         SSO 三步登录 + JWT 自动续期（含纯标准库 AES）
├── translate.py   文档 / 文本翻译
├── review.py      智能评阅（作文）
├── trans_review.py 翻译评阅（译文打分，**不产出译文**）
├── oral_review.py 口语评阅（朗读音频打分）
├── kb_qa.py       知识库问答（RAG，含 v2 RAG 面）
├── rag_v2.py      v2 RAG 面（会话 / 分块级溯源 / 流式问答）
├── sync_ops.py    同步 operation（11 / 13 / 14 / 15 / 17）
├── speech.py      语音合成（operation 9，标准异步链路）
├── question_gen.py 智能出题（op12 + `rm/*` + `ques/*`）
├── image_gen.py   AI 绘画（op10 + `img/*`；风格白名单现取，两级本地校验）
├── article.py     文章写作 / 文本生成（`article/*` + `lm/*` 流式）
├── cli.py         命令行入口
└── errors.py      AigcError / TaskFailed / TaskTimeout / StillRunning / MissingTokenError
~/.config/unipus-aigc/
├── .env           凭证（600）：JWT / rt / 账号 / 加密后的密码
└── secret         本地加密密钥（600，`sso login` 首次自动生成）
docs/call-chains.md    完整调用链（§0.1 实测修正清单；§11 凭证续期）
docs/app-catalog.md    平台应用全景清单 + 可做性分档
docs/agents/           本仓库自己的 agent 工作流约定
.claude/CLAUDE.md      本仓库的开发上下文（刻意不放仓库根，见下）
```

**内容分层**：`SKILL.md` 只装**执行必需**的信息（够跑通就行），
完整的接口记录留在 `docs/`。要深挖接口就去读 `docs/`，不要把它抄进 skill。

**`.claude/CLAUDE.md` 为什么不在仓库根**：plugin 校验器会把根目录的 `CLAUDE.md`
当成"想随 plugin 分发的上下文"并告警。而这个文件是**本仓库开发用**的 agent 工作流
说明，本来就不该分发给使用方。Claude Code 同样会加载 `.claude/CLAUDE.md`，
所以放这里两边都不吃亏，`claude plugin validate . --strict` 也能过。

---

## 几个容易踩的坑

跑通这套流程踩过的坑，写代码前值得先看一眼（细节见 docs/call-chains.md）。
第 4 条是 2026-09-20 实测**纠正**过的，**第 5 条被纠正过两次**（09-20 一次、
09-21 一次才定稿），都与本文件早先的说法相反；第 20 条是 2026-09-21 新增的；
**第 22 条曾经是代码里一个待修的 bug，2026-09-21 已修并复验**；
**第 16、24、25、26 条都是同期实测订正/新增的**。

1. **两个 API 域名都是对的，不是二选一。**
   `uaigc.unipus.cn`（逆向得来的）和 `aigc.unipus.cn`（接口文档里的）指向**同一个后端、
   同一个账号**——实测 operation 9 在两边都提交成功、都拿到音频地址。默认值保持
   `uaigc.unipus.cn` 不动（老链路依赖它），要用文档域名就**显式覆盖**
   `config.API_BASE`，别去改 `config.py` 里的默认值。
   两个域名都不能写成 `ai.unipus.cn`——那只是前端静态资源站。
2. **提交任务必须先连 socket.io 拿 socketId**，否则 `{"code":100,"msg":"socketId不能为空"}`。
   但注意**给哪个值**——不是 `sio.sid`，见第 22 条。
3. **`task/submit` 的 `submitData` 是 JSON 字符串**，而且成功结果在
   `queryTask` 的 `value.responseData` 里，还要再 `json.loads` 一次。
4. **语种码有两套，文档翻译只吃两字母码。** 文本翻译 `eng`/`zho` 和 `en`/`zh` 都行；
   **文档翻译任何一侧用三字母码都会立刻失败**（顶层 `status=4`、`msg="文档翻译失败"`）。
   原因是文档翻译上游是**百度翻译**（`fanyidoc.cdn.bcebos.com`），用的是百度那套码。
   所以文档翻译支持的语种范围由百度决定，**不等于** `translate/lang/list` 那 202 个。
   本项目只实测过 en ↔ zh。
5. **`translate/detail` 的 `status` 是三态**：`2` 进行中 / `1` 成功 / `4` 失败。
   （本文件**两次都写错过**——先写"恒为 `1`"，后写"进行中和成功都是 `1`"。
   2026-09-21 建 `type=2` 文档记录逐秒盯 `detail` 才钉死：`2` 是稳定可复现的，
   不是时序噪声。）
   **判完成只能看 `translation`/`translateUrl` 是否非空**——`status==1` 判完成是对的
   （`1` 就是成功），但别拿 `1` 当"还在跑"。判失败看顶层 `status == 4`，原因在 `msg`。
   `flowResponses[].status` **不可靠**——实测失败时它是 `0` 而不是 `4`，
   拿它判失败会把一个死掉的任务当成"仍在处理中"无限轮询。
6. **评阅结果不要用 `wm/detail` 取**，它的 `evaluation` 永远是 null。
   要拿 `taskId` 查 `task/queryTask`。另外 `*Score` 才是分数，
   去掉 `Score` 的同名字段（`content`/`language`/…）是**评语字符串**；
   `score` 是四项加权求和、量级几千，**不是百分制**。
7. **知识库文档要等 `status == "green"` 且 `chunkSize > 0`**，只看 `ready` 会拿到空答案
   （`ready` 一上传就是 `green`）。解析没完成时提问**返回 200、不报错、答案是空串**。
   这个坑依赖时序：小文档可能几秒就解析完，于是"提前提问"也碰巧成功。
8. **七牛要用 `up-z1.qiniup.com`**，用 `up.qiniup.com` 报 `400 incorrect region`。
9. **长任务不能一次前台阻塞等**。前台命令有 600s 上限，而文档翻译耗时不可预测
   （5 段落 docx 实测 10 秒内，大文件可能几分钟到十几分钟）。一律 submit + poll。
10. **手搓的最小 docx 会被平台拒**。只含 `[Content_Types].xml` + `_rels/.rels` +
    `word/document.xml` 的 zip 会"文档翻译失败"；用 macOS `textutil` 或真正的 Word
    生成的规范 docx 才行。造测试文档时别自己拼 zip。
11. **`translate/list` 看不到纯文本翻译记录（`type=1`），所以 `cleanup` 看不见它们**，
    文本翻译的残留会静默累积。机制见第 20 条：`includeText` 默认 `false`，
    只回 `type=2` 的文档记录。
12. **语音合成的音色表在文档里被压成了一列**（`中文小明zh_ming` 这样名和参数连在一起），
    名参对应关系读不出来。`speech.py` 里的 `SPEAKERS` 是**实测**出来的白名单
    （`zh_ming` / `zh_xiaoxiao` / `zh_youyou` / `en_luka` / `us_annie`），不是抄文档。
13. **拼错的音色会回 HTTP 500，但记录照样建。** `zh_xiaoming` / `en_lukas` / `us_anna`
    这类"很像"的名字提交时报 `server error!`，平台上却留下一条 `audioUrl` 永远为 null
    的记录。所以**别用"提交一下看报不报错"的办法试音色**——CLI 已在提交前拦白名单。
14. **`us_trump` 卡住而不是干净地失败。** 提交成功、拿到 taskId，然后一直停在
    `status=2`，实测 60 秒仍未到终态。轮询等不到结果，所以不在白名单里。
15. **`speech/urlList` 的 `status` 只有 4 态**（1 已提交 / 2 执行中 / 3 成功 / 4 失败），
    跟 `queryTask` 的 7 态**不是同一套编号**，别对着读。另外记录行的 `id`
    **不等于** `taskId`，删记录要用 `id`。
16. **接口文档给的 `submitData` 不一定还认。** AI 绘画（operation 10）的文档
    示例是 `{prompt, reversePrompt, style, size}`，`style` 注释点名
    `manhua/youhua/xieshi/shuicai/gufeng/sd21`——**实测这六个值提交后
    没有一个是原样落地的**：三个被平台静默改写成 `Dall-E-3` 然后
    `taskStatus=4`，两个改成"通用模型一/二"后挂住。只有 `general_v2.1_L`
    （丹青模型）真的回图。
    **但别把这件事读成"那六个不在白名单里"**（本文件早先这么写过，是错的）：
    它们在。`img/getImgReferenceList` 回的线上白名单有 **11 行**，
    那六个**都在里面**。真正的结论更精确也更有用——
    **白名单是必要不充分条件：在表里只说明这个值是合法入参，不说明它会渲染。**
    （这也解释了 `Dall-E-3` 的改写：`azure-dall-e-3` 本来就是白名单里的一行。）
    **实时接口比文档准**：`getImgReferenceList` 比文档多出 `sizeConf`
    （每个风格各自支持的尺寸）和 `styleType`。另外 `size` **必填**
    （缺了直接 `code=100`，这一档不建记录），**失败照样建记录**（`taskStatus=4`
    会留在 `img/queryList` 里），所以 CLI 在发请求前就按白名单挡住——
    **别用"提交一下看报不报错"试 `style` / `size`**。
    详见 [docs/call-chains.md](docs/call-chains.md) §5。
17. **知识库问答有两个 operation，只有 102 能用。** 接口文档的枚举表里
    "知识库问答-新"是 **16**，很容易顺手把 `Operation.KBQA` 改成它——**别改**。
    16 的 `submitData` 是 `{question, qaListId, qaId}`，**根本不收 `kbId`**；
    同一个 `kbId` 下 102 命中知识库内容，16 只回一段固定罐头拒答
    （"抱歉，我无法回答该问题。"）外加一篇与本库无关的 `docSource`，而且
    **不报错**——静悄悄地给错答案，比空字符串更难发现。`source=2` 和
    `source=1715` 两种库都一样。102 的返回里也没有 `qaListId` 字段，
    所以文档那套"续问"流程连第一步都喂不出来。
    16 现在叫 `Operation.KBQA_Doc` 并标注为**文档载但不可用**。
    详见 [docs/call-chains.md](docs/call-chains.md) §3。
18. **"文档有值 ≠ 实测通"已经攒了五个反例**（外加一类不按 operation 编号的
    `article/*` 写侧端点，见第 25 条）。照文档实现新链路前先看一眼
    [docs/call-chains.md](docs/call-chains.md) §5.1：op10（AI 绘画，**已落地**，
    但文档给的 `style` 会被平台改写，见第 16 条）、op12（智能出题，**已跑通**，见第 24 条）、
    op16（KBQA，见上条）、op18（文档问答，参数补到穷尽仍是 `code=100` /
    `code=500` / `执行失败` 三条死路）、**op50（知识掌握总结，见第 21 条）**、
    **op43（AI模型翻译，`responseData` 恒为 `"{}"`，推送帧也是空的）**。
    文档是首选来源，但**不是免检**。
19. **`queryTask` 的 status 枚举不是全集，而且不是所有 operation 都要轮询。**
    11 / 13 / 14 / 15 / 17 是**同步**的，结果就在 `submit` 的响应里，套
    "submit → 轮询"模板会白等到超时（17 的 `docInfo` 还是个 JSON 字符串，
    要再 `loads` 一次）。更麻烦的是实测 op13 回过 **`status=9`**——7 态表里没有，
    客户端**刻意不把它当终态**（读不懂的码既不能算成功也不能算失败），
    于是会轮询到超时；作为补偿，`wait_task` 的超时文案会把真实 status 打出来。
    详见 [docs/call-chains.md](docs/call-chains.md) §5.2。
20. **`translate/list` 的 `includeText` 是"看哪种记录"的开关，不是"带不带正文"。**
    `includeText=false`（默认）**只回 `type=2` 文档翻译**，`true` **只回 `type=1`
    文本翻译**——两种记录在同一个 `list` 里**互斥**，`totalCount` 也跟着变。
    踩坑的地方在于名字：它读起来像"要不要把原文/译文全文带上"，实际是**记录类型**。
    更阴的是显式传的 `type` 参数**被服务端忽略**（`type=1` / `type=2` 与不传等价），
    所以"我明明筛了 type"是个错觉。上面第 11 条就是这个机制的表现。
21. **`operation 50`「知识掌握总结」是个空壳：`analysis` 恒为 `"暂无总结"`。**
    文档（`64754844`）把它当异步 operation 写着，`submitData` 只要
    `{"kbId": "…"}`，传输层也真的通——但**拿一个根本不存在的 `kbId`
    （`KB0000…0000`）会得到逐字节相同的返回**。也就是说这个字段
    **既不能说明库里有没有内容，也不受 `doSummary` 影响**（手动调
    `rag/kbp/project/doSummary` 返回 `true` 后重提交，结果不变）。
    前后对照做过：同一个库 `kb ask` 能答对、`summary` 有内容，
    `chunkSize > 0`，唯独 op50 是空的。
    **不要拿它给用户做"知识掌握总结"**——用户会以为自己的库是空的。
    记为「文档载但业务不通」，跟 op10/op12/op16/op18 同一类。
    详见 [docs/call-chains.md](docs/call-chains.md) §5.1。
22. **`socketId` 要拼成 `{appId}:{openId}:{devId}`，不能给裸 sid。**（2026-09-21 实测）
    文档（`64758329`）明写拼接方式，示例是
    `1200:d5187603…:ff37dcf6…`；代码**当时**给的是 `sio.sid`（20 字符、无冒号）。
    **两种都能过校验**（`task/submit` 都返回 `taskId`），**但只有拼接串能收到
    `msg_aigc` 推送**——裸 sid 那条链路推送**静默丢失**，一条都收不到，
    任务照样跑完、`queryTask` 照样回 `status=3`，不报任何错。
    所以"推送不可靠"这句老话，**一部分是我们给错了投递地址**，不是服务端不稳。
    **已修（2026-09-21）**：`client.connect_socket` 现在返回拼接串，
    不再返回 `sio.sid`。
23. **op102（知识库问答）的答案只走 socket 推送，`responseData` 恒为 `null`。**
    文档 L1001 那句"任务会去 QAnything 框架获取答案信息并将结果发送给 socketIo"
    是准确的：`queryTask` 只是个状态机（`status` 会走到 `3`），
    **答案本身是一帧 `msg_aigc` 推送**。所以"`status=3` 但 `responseData` 是 null"
    **不是失败**，是答案走了另一条路——而第 22 条那个形态 bug（**已修**）
    曾经让这条路走不通。
    另外 op102 的 `submitData` 里 `source` 和 `networking` **服务端都不看**：
    带与不带返回完全一致，别再"两个都发"。
24. **智能出题（op12）能跑了，但有四个坑。**（2026-09-21 全链路实测）
    链路是 **`rm/create` 拿 `rmId` → op12 出题 → `ques/accept` 采纳 →
    `ques/generationQuesJson` 取题面**。四条必须知道：

    * **`rm/create` 回的是 `rmId`，不是文档写的 `id`**（`55226315` L2824 的字
      段表写错）。文档里那个"差一个拿不到的 `rmId`"的老结论因此作废。
    * **§2.1 `ques/generation` 是死路，别照它实现出题。** 它同步就回题面、
      看着最省事，但**不落库、没有 `quesId`**，采纳不了——文档那张返回表是空的。
      它只剩一个用处：**不留残留地试策略组合**（CLI 里是 `questions preview`）。
    * **`rm/delete` 不存在，阅读材料建了就删不掉**（`rm` 只有
      create/detail/update/list 四个端点）。**别为试参数随手建材料**，
      要长期测试就复用同一条。
    * **op12 不是每次都成功，且失败也建记录。** 同一个 `--ploy 1010:1`
      一次通、一次 `status=4`。失败时 `ques/generationQuesList` 照样多一行
      `pid`（ploy 列表为空）但 **`generateCount` 不加一**——判断成败看
      `generateCount`，别数 `pid` 行。

    另有字段名陷阱：记录里的代码字段叫 **`quesCode`** 不是 `code`（读错拿 `None`
    且不报错）；`ques/generationQuesList` 在没有记录时抛 `code=1001`，
    那是**正常空状态**不是故障。
    详见 [docs/call-chains.md](docs/call-chains.md) §9。
25. **`article/*` 里那三个"写侧"端点是空壳，真正在用的是 `lm/*`（SSE 流式）。**
    （2026-09-21 实测）接口文档把 `article/aiTextOperation`（续写/扩写/优化）、
    `article/aiOperation`（一级大纲/二级大纲/正文）、`article/aiOptimizeArticle`
    的返回表写得很具体，**实测三个的 `value.content` 恒为 `null`，而且不看入参**：
    给一个**根本不存在的 `articleId`** 打 `aiTextOperation`，
    回来的是**逐字节相同**的 `{"content": null}`——跟第 21 条的 op50 是同一类。
    **旁证是决定性的：前端产物里根本没有这三个端点**
    （`uaigc_index.js` 有 `aiTitle` / `insertArticle` / `delete`，没有它们），
    这是**第一例"文档写了、前端从没用过"**。所以别照文档实现它们。
    **真正在用的是 `lm/*`**：`generate/outline` 是普通 JSON，
    `content/continueWrite` / `content/commonContinueWrite` / `rewrite/content`
    是 **SSE 流式**（`text/event-stream`，帧是
    `data:{"choices":[{"delta":{"content":"…"}}]}`，`[DONE]` 收尾）。
    **注意：这是 SSE over HTTP，不是 socket.io 推送**——上一版把文章写作记成
    "要消费 Socket.IO 增量推送"，**那个猜测是错的**。
    SSE 的响应头**不带 charset**，不把 `resp.encoding` 钉成 utf-8 中文会变成
    `ä¸æ`（跟 v2 RAG 流式是同一个坑）。
    另有两处**文档标 false、实测必填**：`getArticleList` 的 `templateType`、
    `aiOptimizeArticle` 的 `tone`（后者所在端点本身是空壳）。
    ⚠️ 还有一处**"不报错但结果错"**：`article/aiTitle` 的 `articleId` 不给也
    "成功"、照样回 10 条标题，**但跟你的文章毫无关系**（同一篇文章，给了 id 出
    「AI 如何重塑教育的未来」，不给则出「如何让生活更高效」这类泛标题）。
    详见 [docs/call-chains.md](docs/call-chains.md) §10。
26. **凭证可以自动续期，但有两个坑**（2026-09-21 新增，见
    [docs/call-chains.md](docs/call-chains.md) §11）：

    * **「unipus SSO 无独立 refresh 接口」是错的。** 接口
      `/sso/4.0/sso/refresh_jwt` 在 `aigc_index.js` 里，**不在 SSO 自己的包
      `usso.min.js` 里**——只看后者会得出错误结论。实测用 `rt` 换新 JWT 通，
      且 **`rt` 不轮换**（换回来的跟传进去的一样），所以能反复用。
    * **登录表单的 AES key 是公开常量**（`8AD70B64…`，来自 `usso.min.js`）。
      所以那层加密只保证账号密码不裸奔，**不是认证**，拿到它也绕不开密码。

    另有两个**开发期**踩到的坑，不属于接口范畴但也记一笔：
    `./.env` 在优先级上**高于**用户级文件，只更新后者会让续期"看起来生效其实没生效"
    （现在会把新 JWT 同步过去，但只同步 token 那一行）；
    以及"拿到新凭证"和"写盘成功"混在一个返回值里会导致 `KeyError` 被
    `except Exception` 吞掉——两个坑都是**伪装成过期跑一遍**才暴露的。

## 注意

- **凭证有效期**：配了 `sso login` 的**不用管**——JWT 到期前 5 分钟自动换新。
  只粘了 JWT 的，48 小时后要再粘一次；`run.sh token` 会提示剩余天数。
- **`.env` 已在 `.gitignore` 里，不要提交。** `~/.config/unipus-aigc/.env` 在仓库外，
  但同样是明文凭证，权限 `600`。
  **用了 `sso login` 之后那个文件里还多一份加密的账号密码**——加密密钥
  （`secret`）默认就在同目录，所以这两件事的门槛一样，别只搬走 `.env`。
- **删除类操作一律先干跑**。`cleanup` / `kb delete` / `kbv2 delete` / `oral delete` /
  `trans-review delete` / `questions delete` / `article delete` / `image delete` /
  `sso forget`，**不带 `--yes` 都只列不删**。
  **skill 不会替用户按 `--yes`**，也不会引导用户"直接加 `--yes` 就行"。
- **平台不留测试垃圾**。跑完记得看一眼 `records`；删不掉的（比如出题的阅读材料）
  要如实说明，别假装删了。

## 开发

本仓库的 agent 工作流约定（issue tracker、triage 标签、领域文档）在
[.claude/CLAUDE.md](.claude/CLAUDE.md) 和 [docs/agents/](docs/agents/)。
进行中的改造记录在 [.scratch/](.scratch/)。

```bash
python3 -m compileall -q lib/                 # 语法
claude plugin validate . --strict             # 应当通过，无告警
claude --plugin-dir .                         # 起一个会话实测 skill 加载与触发
```

**加新应用时的顺序**：先在 [docs/app-catalog.md](docs/app-catalog.md) 认领线索，
照 [docs/call-chains.md](docs/call-chains.md) §4 的模板补一条竖切，
**每一处都真跑一遍**（文档是首选来源，但不是免检），把结论写回 `docs/`，
最后才做 `SKILL.md` —— **skill 里只放执行必需的信息**。
