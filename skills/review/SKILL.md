---
name: review
description: >-
  Review and grade English essays through the Unipus AIGC platform (智能评阅). Use when the user asks to score, grade, review, or give feedback on
  an English composition — including per-sentence grammar correction with
  rewrite suggestions. 通过 Unipus AIGC 平台做英语作文智能评阅：总分、四项分项、
  逐句纠错与改写建议。
---

# Unipus AIGC · 智能评阅（作文）

给一篇英语作文打分，返回总分、四个分项、语言特征统计、以及**逐句**的纠错与改写建议。

## 先问清楚三件事

1. **作文正文** —— 直接给文字，或者给一个文件路径（`.txt` 等纯文本）。
2. **学段 `--level`** —— 这个**必须问**，它直接影响评分标准：

   | 值 | 学段 |
   | --- | --- |
   | `0` | 大学（默认） |
   | `1` | 高中 |
   | `2` | 初中 |
   | `3` | 小学 |

3. **题目 `--topic`** —— 可选。不给的话会用标题兜底；给了能让评语更贴合。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" review <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上的 `NotOpenSSLWarning` 是环境噪声，**只看退出码判断成败**。

凭证没配或过期（退出码 1）→ 让用户走 `/unipus-aigc:guide`。

## 推荐：一次性评阅

评阅通常几十秒内出结果，不会撞 600 秒上限，所以一次调用即可：

```bash
bash "$S/scripts/run.sh" review essay --path /绝对路径/essay.txt --level 1 --topic "AI and Homework"
```

或者直接给正文：

```bash
bash "$S/scripts/run.sh" review essay --text "作文内容..." --level 0
```

stdout 是排好版的可读报告（总分、分项、字数统计、总评、逐句纠错）。
加 `--json` 改成输出原始 JSON。

## 拆两步（作文很长、或想避免阻塞时）

```bash
bash "$S/scripts/run.sh" review submit --path /绝对路径/essay.txt --level 1
#   → 输出 wmId 和 taskId

bash "$S/scripts/run.sh" review poll <taskId> --wait 60
#   → 退出码 0 出结果；退出码 3 表示还在跑，用同一个 taskId 再 poll
```

**查结果用 `taskId`，不是 `wmId`。** 这条最容易搞错。

只查一次不等待：`review get <taskId>`（未完成则退出码 3）。

## 坑 1：别用 `wm/detail` 取结果

`wm/detail` 的 `evaluation` 字段**永远是 `null`**，轮询几十次也不会填充。
评阅结果**只能**拿 `taskId` 去查 `task/queryTask`。CLI 已经这么做了，
只有你绕过 CLI 直接读接口时才会踩到。

## 坑 2：`*Score` 是分数，去掉 `Score` 的同名字段是评语字符串

结果里同时有 `contentScore` 和 `content`、`languageScore` 和 `language`……
**带 `Score` 后缀的才是数字分数**，不带的是**评语文本**。搞混了会把一段中文评语
当成分数打印出来。

## 坑 3：总分不是百分制

`score` 是**四个分项加权求和**，量级在几千，不是 100 分制。实测一篇 54 词的作文：

```
总分: 2501
分项: 内容 1280   语言 690   结构 330   规范 200      # 相加 ≈ 2501
```

另有 `totalGrade`（实测 `3.0`）是等级制的另一套刻度。
**向用户汇报时不要说"得了 2501 分"**，那会让人误读；说清楚是加权总分、
并把四个分项一起给出，或者直接转述 `comment` 里的总评。

## 结果结构

```jsonc
{
  "score": 2501,                  // 加权总分 = 四个分项之和
  "totalGrade": 3.0,
  "contentScore": 1280,           // 内容
  "languageScore": 690,           // 语言
  "organizationScore": 330,       // 结构
  "mechanicsScore": 200,          // 规范
  "comment": "词汇表达匮乏，建议增加高级词汇积累……",   // 总评（多行文本）
  "feature": {                    // 语言特征统计
    "tokens": 54, "sentCount": 5, "paraCount": 1, "avgSentLen": 12,
    "TTR": 1.0, "fleschReadingEase": 89.6, "fleschKincaidGradeLevel": 3.0
  },
  "correct": [                    // 逐句纠错
    {
      "index": "1.1",
      "content": "Many student uses AI tools to help they finish homework.",
      "suggest_sent": "Many students use AI tools to help them finish homework.",
      "errorList": [
        { "typeName": "名词的数错误", "typeId": "WS-N1",
          "words": [{"i": 5, "j": 12}],          // 错误在句中的字符区间
          "desc": " student  建议改为 students 。解析：student是可数名词……",
          "suggest_sent": "Many students use AI tools to help them finish homework." }
      ]
    }
  ]
}
```

常见 `typeId` 前缀：`WS-N1` 名词的数、`WS-V1` 主谓一致、`WS-P0` 代词错用、
`WO-O` 词法、`WS-VO` 动词错用、`WM-D` 限定词缺失。

`words[].i` / `words[].j` 是错误在**该句**里的字符区间，可以用来做高亮。

## 汇报给用户时

- 先给总评（`comment`）和四个分项，别一上来堆 JSON。
- 逐句纠错按 `index` 顺序列，每句给「原句 → 建议句」，再列错误类型与解析。
- 一篇作文可能有十几条错误，**全列出来**，不要只挑前几条——用户要的就是这个。

## 结果的存放位置

每次成功都会把原始 JSON 落到 `~/.cache/unipus-aigc/review-<taskId>.json`，
路径打在 stderr 上。要完整结构就读那个文件，不要反复调接口。

## 收尾

评阅会在平台上留记录（`wmId`）。用户想清理时**不要自己动手**——
让他走 `/unipus-aigc:guide` 的 `records` / `cleanup`，那里有安全约束。

## 范围之外

本 skill 只做**作文**评阅（`operation:35`、`wm` 的 `type:"1"`）。用户问到别的评阅或
任何其它平台能力（"还能干什么"）时，**不要猜、也不要直接说没有**——转给对应的 skill：

| 用户想要 | 去哪 |
| --- | --- |
| 给一份**译文**打分 | `unipus-aigc:trans-review`（`operation:36`、`type:"2"`） |
| 给一段**朗读音频**打分 | `unipus-aigc:oral-review`（`operation:90`、`type:"3"`） |
| **改**译文 / 润色译文 | `unipus-aigc:translate`——`trans-review` **只打分，不产出译文** |
| 平台的完整应用清单 | `/unipus-aigc:guide`（25 个应用 + 可做性分档） |

> "译后编辑"这个说法在本仓库里是**错的**，指的是 `trans-review`——那个应用实测
> 只回一个 `score`，一个字的译文都没有。**别沿用这个说法跟用户解释。**

## 更深的材料

调用链与接口清单见 plugin 仓库的 `docs/call-chains.md` §2。
翻译评阅见 §8，口语评阅见 §7。
