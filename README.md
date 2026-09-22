# auto-aigc-tasks

Unipus AIGC 平台的 Claude Code plugin：把平台上的应用封装成 skills。

## 环境安装

```bash
claude plugin marketplace add https://github.com/ChenYu1991ppak/Unipus-AIGC-skills
claude plugin install unipus-aigc@Unipus-AIGC-skills
```

装完**重启** Claude Code 才生效。

Python 依赖要单独装（plugin 不自带）：

```bash
python3 -m pip install -r requirements.txt
```

需要 `requests`、`python-socketio`、`websocket-client`；代码兼容 **Python 3.9+**。

## 入口：`/unipus-aigc:guide`

`guide` 是总入口，管两件事：

1. **检查凭证** —— 看有没有存过账号密码。存过就把 token 更新好，之后每个应用都会
   自动续期，不用再管；没存过就问你要账号和密码（也提供手动更新的方式）。
2. **路由** —— 按你想做的事，把你送到对应的应用 skill。

**样例**（在 Claude Code 里）：

```
/unipus-aigc:guide
```

它会检查凭证、必要时问你要账号密码，然后问你想做什么。比如你接着说：

```
帮我翻译这段话：The library opens at eight in the morning.
```

它就会带你走完翻译。

> 凭证等于账号密码，**不要提交到仓库、不要转发给任何人**。

### 另一个入口：`/unipus-aigc:task-gen`

`task-gen` **不走 `guide` 路由**，单独唤起。它负责生成教学任务清单——
英语朗读评测、英语写作、阅读理解练习、翻译练习、看图作文、听力练习。
**任务是现写的**（不是从题库里抽的）：每条都是一次真实的课堂练习，
写明交给哪个应用执行、素材从哪来；清单里每条都带一句可以交给 `guide`
去执行的话。生成过程不调用平台。

```
/unipus-aigc:task-gen
帮我出一套高中英语的练习任务
```

## 功能

| skill | 做什么 |
| --- | --- |
| `guide` | 总入口：检查/配置登录凭证、续期 token、路由到其它 skill |
| `translate` | 文本翻译、文档翻译（docx / pdf 等） |
| `review` | 英语作文智能评阅：总分、分项、逐句纠错与改写建议 |
| `trans-review` | 翻译评阅：给一份译文打分（**只出分数，不产出译文**） |
| `oral-review` | 口语评阅：给一段朗读音频打分 + 逐词发音反馈 |
| `kb-qa` | 知识库问答（RAG）：就自己的文档提问，答案带出处；支持多轮追问与分块级溯源 |
| `speech` | 语音合成（TTS）：把文字念成音频 |
| `question-gen` | 智能出题：根据阅读材料生成题目，可审题、采纳、导出 |
| `image-gen` | 图像生成（AI 绘画 / 文生图）：按描述出图 |
| `text-gen` | 文本生成 / 文章写作：起标题、列大纲、续写、改写 |
| `task-gen` | 生成教学任务清单：朗读评测 / 写作 / 阅读理解 / 翻译 / 看图作文 / 听力。任务是**现写**的，每条写明交给哪个应用、素材从哪来；不调用平台，任务交给 guide 执行 |
