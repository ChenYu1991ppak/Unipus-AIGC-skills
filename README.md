# auto-aigc-tasks

`ai.unipus.cn` 上三个 AIGC 应用（**文档翻译 / 知识库问答 / 智能评阅**）的 Python 客户端。

平台的调用链是黑盒逆向出来的，完整说明见 [docs/call-chains.md](docs/call-chains.md)；
平台上还有哪些应用（共 25 个）以及各自写调用链的难度，见
[docs/app-catalog.md](docs/app-catalog.md)。

## 安装

```bash
pip install -r requirements.txt
```

依赖：`requests`、`python-socketio`、`websocket-client`。

## 配置 token

客户端从环境变量 `UNIPUS_AIGC_TOKEN` 或项目根目录的 `.env` 读取 JWT：

```bash
cp .env.example .env
# 编辑 .env，填入 UNIPUS_AIGC_TOKEN=<JWT>
```

JWT 获取方式：登录 <https://ai.unipus.cn>，从浏览器开发者工具
localStorage 的 `userInfo.jwt` 复制。`openId` 会自动从 JWT 里解出来，不用手填。

先确认 token 还有效：

```bash
python -m unipus_aigc.cli token
```

## 命令行用法

```bash
# 文本翻译
python -m unipus_aigc.cli translate-text "Hello, world."

# 文档翻译，译文存到本地
python -m unipus_aigc.cli translate-doc ./paper.pdf --to zh --out ./paper.zh.docx

# 智能评阅（--level 0大学 1高中 2初中 3小学）
python -m unipus_aigc.cli review --path essay.txt --topic "AI and Study" --level 0

# 知识库问答：临时建库、传文档、提问、自动删库
python -m unipus_aigc.cli kb-ask "文档里的暗号是什么？" --doc ./doc.txt

# 也可以对已有知识库提问
python -m unipus_aigc.cli kb-ask "暗号是什么？" --kb KBxxxxxxxx

# 列出历史记录 / 清理测试数据
python -m unipus_aigc.cli records
python -m unipus_aigc.cli cleanup --ids <id> --yes
```

## 作为库使用

```python
from unipus_aigc import UnipusAIGC, ReviewAPI

with UnipusAIGC() as cli:                       # 自动管理 socket.io 连接
    # 1. 翻译
    result = cli.translate.text("The quick brown fox jumps over the lazy dog.", "en", "zh")
    print(cli.translate.translated_text(result))

    # 文档翻译：cli.translate.document("./a.pdf", "en", "zh") -> value.translateUrl

    # 2. 智能评阅
    report = cli.review.essay("Many student uses AI tools to help they finish homework.",
                              topic="AI and Study", level=0)
    print(ReviewAPI.format_report(report))
    print(report["score"], report["contentScore"])

    # 3. 知识库问答
    kb_id = cli.kb.create("我的知识库")
    cli.kb.upload_document(kb_id, "./doc.txt")          # 等到解析完成才返回
    answer = cli.kb.ask(kb_id, "文档讲了什么？")
    print(cli.kb.answer_text(answer), answer["sourceUrl"])
```

## 结构

```
unipus_aigc/
├── config.py      主机地址、token 读取、JWT 解析
├── client.py      UnipusAIGC：请求头 + POST + 七牛上传 + socketId + 任务轮询
├── constants.py   操作码、状态码、学段、ISO-639-2 语言码
├── translate.py   文档 / 文本翻译
├── review.py      智能评阅（作文）
├── kb_qa.py       知识库问答（RAG）
├── cli.py         命令行入口
└── errors.py      AigcError / TaskFailed / TaskTimeout
docs/call-chains.md    逆向出来的完整调用链（已完成的三条）
docs/app-catalog.md    平台应用全景清单 + 剩余应用的可做性分档
```

## 几个容易踩的坑

跑通这套流程踩过的坑，写代码前值得先看一眼（细节见 docs/call-chains.md）：

1. **API 主机是 `uaigc.unipus.cn`，不是 `ai.unipus.cn`** —— 后者只有前端静态资源。
2. **提交任务必须先连 socket.io 拿 sid**，否则 `{"code":100,"msg":"socketId不能为空"}`。
3. **`task/submit` 的 `submitData` 是 JSON 字符串**，而且成功结果在
   `queryTask` 的 `value.responseData` 里，还要再 `json.loads` 一次。
4. **翻译的语种要 ISO-639-2 三字母码**（`eng`/`zho`），传 `en`/`zh` 报 12003。
5. **`translate/detail` 的 `status` 恒为 1**，别拿它判断任务是否完成。
6. **评阅结果不要用 `wm/detail` 取**，它的 `evaluation` 永远是 null。
7. **知识库文档要等 `status == "green"` 且 `chunkSize > 0`**，只看 `ready` 会拿到空答案
   （不报错，静默返回空串）。
8. **七牛要用 `up-z1.qiniup.com`**，用 `up.qiniup.com` 报 `400 incorrect region`。

## 注意

- token 有效期约 24 小时（看 JWT 的 `exp`），过期后重新登录复制。`cli token` 会提示剩余天数。
- `.env` 已在 `.gitignore` 里，不要提交。
- `cli cleanup` 默认只列出不删除，加 `--yes` 才真删。用 `--ids` 精确指定比 `--pattern` 安全。
  平台自带的"默认知识库"不会被删。
