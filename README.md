# auto-aigc-tasks

`ai.unipus.cn`（Unipus AIGC 平台）的 **Claude Code plugin**。

把平台上八个已跑通的 AIGC 应用——**文档/文本翻译、智能评阅（作文）、翻译评阅（译文打分）、
口语评阅（朗读音频打分）、知识库问答（RAG）、语音合成（TTS）** 等——封装成 skills，
让 Claude 能直接引导用户完成调用；底下是一个
Python 客户端（`lib/unipus_aigc/`），**skill 是壳，Python 是手**。

调用链**首选接口文档**（Confluence 55226315 / 64754844），逆向只做兜底：

- **接口文档** —— 已实现的**全部**应用都照它走，包括文档翻译 / 作文评阅 /
  知识库问答这三条早先标成"逆向"的链路（2026-09-21 订正，端点全在文档里）。
- **黑盒逆向**（前端 webpack 产物 + 抓包）—— 只剩**极少数文档没写的字面量**，
  五处，逐条列在 [docs/call-chains.md](docs/call-chains.md) §0.1，
  **每一处都实测过，没有一处是"文档写错了"**。

- 完整调用链记录：[docs/call-chains.md](docs/call-chains.md)
- 平台上全部应用的可做性分档：[docs/app-catalog.md](docs/app-catalog.md)

## 授权与合规（先读这段）

**平台方没有提供开放 API。** 这里的调用链是对**自己账号**的接口整理
（以内部接口文档为准，文档没覆盖处以黑盒逆向补齐：前端 webpack 产物 + 抓包验证）。
只覆盖登录后**本就能用**的功能。因此：

- 只适合**在平台上有正当账号的人，用他们自己的凭证**使用。
- 不要拿别人的 token，不要用它对平台做压测或批量抓取。
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

## 开始用

在 Claude Code 里运行：

```
/unipus-aigc:guide
```

它会带你配凭证、体检、并按需求把你路由到对应的应用 skill。

| skill | 触发方式 | 管什么 |
| --- | --- | --- |
| `guide` | **只能用户唤起** `/unipus-aigc:guide` | 配凭证、体检 token、`records`/`cleanup`、平台应用的路由 |
| `translate` | 模型可自触发 | 文本翻译、文档翻译 |
| `review` | 模型可自触发 | 英语作文智能评阅 |
| `trans-review` | 模型可自触发 | 翻译评阅（译文打分，**不产出译文**） |
| `oral-review` | 模型可自触发 | 口语评阅（朗读音频打分 + 发音反馈） |
| `kb-qa` | 模型可自触发 | 知识库问答（RAG） |
| `speech` | 模型可自触发 | 语音合成（文字转音频） |

`guide` 带 `disable-model-invocation: true`，所以**模型看不见它、也无法代你触发**。
这是有意的：它管凭证，不该在无关对话里被自动唤起。代价是"这个平台还能干什么"
这类问题模型会错配到某个应用 skill——每个应用 skill 里都写了兜底提示，
会把用户转回 `/unipus-aigc:guide`。

## 配置凭证

JWT 是**你自己的登录凭证，等于账号密码**。

获取方式：登录 <https://ai.unipus.cn> → 浏览器开发者工具 → Local Storage →
`userInfo` 里的 `jwt` 字段。`openId` 会自动从 JWT 解出来，不用手填。

**推荐走 skill**：把 JWT 告诉 Claude，`/unipus-aigc:guide` 会调 `set-token` 落盘，
回显只有路径、sha256 指纹和有效期，**不复述 token 本身**。

读取优先级（先到先得）：

1. 环境变量 `UNIPUS_AIGC_TOKEN`
2. `./.env`（当前工作目录）
3. `~/.config/unipus-aigc/.env` ← `set-token` 默认写这里，权限 `600`、目录 `700`

第 3 条是装成 plugin 后的常态路径——**plugin 场景下 CWD 不再是仓库根**，
只查 `./.env` 会静默读不到 token。覆盖旧值时会自动备份成 `<path>.bak`。

```bash
cp .env.example .env        # 只在仓库内开发时用这条路
```

体检：

```bash
bash skills/guide/scripts/run.sh token
```

## 命令行用法

每个 skill 目录下有一个自定位的壳脚本，**与 CWD 无关**：

```bash
S=skills/translate/scripts/run.sh

# 文本翻译（秒级）
bash $S translate text "Hello, world."

# 文档翻译：必须拆两步，前台调用有 600s 上限
bash $S translate submit-doc ./paper.pdf --to zh      # 秒级返回记录 id
bash $S translate poll <记录id> --out ./paper.zh.docx  # 可反复调用；退出码 3 = 还在跑

# 智能评阅（--level 0大学 1高中 2初中 3小学）
bash skills/review/scripts/run.sh review essay --path essay.txt --topic "AI and Study" --level 0

# 知识库问答：临时建库、传文档、提问、答完自动删库
bash skills/kb-qa/scripts/run.sh kb ask "文档里的暗号是什么？" --doc ./doc.txt
# 也可以对已有知识库提问
bash skills/kb-qa/scripts/run.sh kb ask "暗号是什么？" --kb KBxxxxxxxx

# 语音合成（秒级）
bash skills/speech/scripts/run.sh speech speakers                 # 可用音色
bash skills/speech/scripts/run.sh speech say "要念的文字" --speaker zh_youyou --out ./a.mp3

# 列出历史记录 / 清理测试数据（guide 域）
bash skills/guide/scripts/run.sh records
bash skills/guide/scripts/run.sh cleanup --ids <id> --yes
```

也可以绕过壳脚本直接调：

```bash
PYTHONPATH=lib python3 -m unipus_aigc.cli <子命令> ...
```

**退出码**：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上可能有一条 urllib3 的 `NotOpenSSLWarning`，那是环境噪声——
**判断成败只看退出码**。

## 作为库使用

```python
from unipus_aigc import UnipusAIGC, ReviewAPI

with UnipusAIGC() as cli:                       # 自动管理 socket.io 连接
    # 1. 文本翻译
    result = cli.translate.text("The quick brown fox jumps over the lazy dog.", "en", "zh")
    print(cli.translate.translated_text(result))

    # 2. 文档翻译：长任务，用 submit + poll，不要阻塞等
    rec = cli.translate.submit_document("./a.pdf", "en", "zh")   # 秒级返回
    detail = cli.translate.poll(rec["id"], timeout=60)           # 未完成抛 StillRunning
    print(detail["translateUrl"])
    # 阻塞版 cli.translate.document(...) 仍在，但在 600s 上限的环境里别用

    # 3. 智能评阅
    report = cli.review.essay("Many student uses AI tools to help they finish homework.",
                              topic="AI and Study", level=0)
    print(ReviewAPI.format_report(report))
    print(report["score"], report["contentScore"])

    # 4. 知识库问答
    kb_id = cli.kb.create("我的知识库")
    cli.kb.upload_document(kb_id, "./doc.txt")          # 等到解析完成才返回
    answer = cli.kb.ask(kb_id, "文档讲了什么？")
    print(cli.kb.answer_text(answer), answer["sourceUrl"])
```

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
└── speech/      SKILL.md + scripts/run.sh
lib/unipus_aigc/
├── config.py      主机地址、凭证读取与写入、JWT 解析
├── client.py      UnipusAIGC：请求头 + POST + 七牛上传 + socketId + 任务轮询
├── constants.py   操作码、状态码、学段、两套语种码
├── translate.py   文档 / 文本翻译
├── review.py      智能评阅（作文）
├── trans_review.py 翻译评阅（译文打分，**不产出译文**）
├── oral_review.py 口语评阅（朗读音频打分）
├── kb_qa.py       知识库问答（RAG，含 v2 RAG 面）
├── rag_v2.py      v2 RAG 面（会话 / 分块级溯源 / 流式问答）
├── sync_ops.py    同步 operation（11 / 13 / 14 / 15 / 17）
├── speech.py      语音合成（operation 9，标准异步链路）
├── cli.py         命令行入口
└── errors.py      AigcError / TaskFailed / TaskTimeout / StillRunning / MissingTokenError
docs/call-chains.md    完整调用链（全部按文档实现；§0.1 是实测修正清单）
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

## 几个容易踩的坑

跑通这套流程踩过的坑，写代码前值得先看一眼（细节见 docs/call-chains.md）。
第 4 条是 2026-09-20 实测**纠正**过的，**第 5 条被纠正过两次**（09-20 一次、
09-21 一次才定稿），都与本文件早先的说法相反；第 20 条是 2026-09-21 新增的；
**第 22 条曾经是代码里一个待修的 bug，2026-09-21 已修并复验**。

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
    `manhua/youhua/xieshi/shuicai/gufeng/sd21`——**实测六个值里没有一个能出图**：
    三个被平台静默改写成 `Dall-E-3` 然后 `taskStatus=4`，两个挂住不动。
    只有 `general_v2.1_L`（丹青模型）真的回图，而它**不在文档列表里**。
    **实时接口比文档准**：`img/getImgReferenceList` 才是风格白名单的真源，
    它比文档多出 `sizeConf`（每个风格支持的尺寸）和 `styleType`。
    另外 `size` **必填**（缺了直接 `code=100`），失败照样建记录。
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
18. **"文档有值 ≠ 实测通"已经攒了五个反例。** 照文档实现新链路前先看一眼
    [docs/call-chains.md](docs/call-chains.md) §5.1：op10（AI 绘画，文档给的
    `style` 全被静默改写）、op12（智能出题，字段名确认了但拿不到真题模 `rmId`）、
    op16（KBQA，见上条）、op18（文档问答，参数补到穷尽仍是 `code=100` /
    `code=500` / `执行失败` 三条死路）、**op50（知识掌握总结，见第 21 条）**。
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
    实测：`.scratch/app-expansion/probes/probe_translate_list_final.py`
    （`false` → 10 行全是 `type=2`，`true` → 9 行全是 `type=1`）。
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
    不再返回 `sio.sid`。复验走的是**真实代码路径**——修复后的
    `client.socket_id` 收 1 条推送，对照的裸 `sio.sid` 收 0 条，两边
    `queryTask.status` 都是 `3`。探针：
    `.scratch/app-expansion/probes/probe_socketid_after_fix.py`。
23. **op102（知识库问答）的答案只走 socket 推送，`responseData` 恒为 `null`。**
    文档 L1001 那句"任务会去 QAnything 框架获取答案信息并将结果发送给 socketIo"
    是准确的：`queryTask` 只是个状态机（`status` 会走到 `3`），
    **答案本身是一帧 `msg_aigc` 推送**。所以"`status=3` 但 `responseData` 是 null"
    **不是失败**，是答案走了另一条路——而第 22 条那个形态 bug（**已修**）
    曾经让这条路走不通。
    另外 op102 的 `submitData` 里 `source` 和 `networking` **服务端都不看**：
    带与不带返回完全一致，别再"两个都发"。

## 注意

- token 有效期看 JWT 的 `exp`（实测有 24h 也有 48h 的），过期后重新登录复制。
  `run.sh token` 会提示剩余天数。
- `.env` 已在 `.gitignore` 里，**不要提交**。`~/.config/unipus-aigc/.env` 在仓库外，
  但同样是明文凭证，权限已设 `600`。
- `cleanup` / `kb delete` 默认只列出不删除，加 `--yes` 才真删。用 `--ids` 精确指定
  比 `--pattern` 安全。平台自带的"默认知识库"不会被删。
  **skill 不会替用户按 `--yes`。**

## 开发

本仓库的 agent 工作流约定（issue tracker、triage 标签、领域文档）在
[.claude/CLAUDE.md](.claude/CLAUDE.md) 和 [docs/agents/](docs/agents/)。
进行中的改造记录在 [.scratch/plugin-migration/](.scratch/plugin-migration/)。

```bash
claude plugin validate . --strict     # 应当通过，无告警
claude --plugin-dir .                 # 起一个会话实测 skill 加载与触发
```
