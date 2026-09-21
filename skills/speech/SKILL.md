---
name: speech
description: >-
  Synthesize speech from text through the Unipus AIGC platform —
  turn a passage into an mp3 audio file. Use when the user asks for 语音合成 /
  TTS / 朗读 / 配音 / 把文字转成音频 / 生成听力音频, or wants an audio version
  of some text. Also covers listing the available 音色 (speakers).
  通过 Unipus AIGC 平台做语音合成（文字转音频），并列出可用音色。
---

# Unipus AIGC · 语音合成

把一段文字念成 mp3。秒级任务，**不用**像文档翻译那样拆两步。

## 先问清楚两件事

1. **念什么** —— 文字内容。太长的话先提醒用户：合成结果是一个整段音频，
   没有分页，几万字的文本跑起来很慢而且未必是用户想要的。
2. **用什么音色** —— 看下面的音色表。用户没指定就默认 `zh_youyou`（中文女声），
   并**告诉他你选了哪个**。

语速、音量、音频格式都有默认值，用户不提就别问。

## 命令入口

把 `$S` 换成**本 skill 的基目录**（加载时会告知 `Base directory for this skill: ...`）：

```bash
S="<本 skill 基目录>"
bash "$S/scripts/run.sh" speech <子命令> [参数...]
```

退出码：`0` 成功 / `1` 真失败 / `2` 用法错 / **`3` 仍在处理中（不是错误）**。
stderr 上的 `NotOpenSSLWarning` 是环境噪声，**只看退出码判断成败**。

凭证没配或过期时（退出码 1）→ 让用户走 `/unipus-aigc:guide`。

## 合成：一次调用

```bash
bash "$S/scripts/run.sh" speech say "要念的文字" --speaker zh_youyou --out /绝对路径/a.mp3
```

通常 3~10 秒出结果，stdout 有**音频地址**，给了 `--out` 就已落盘。
加 `--json` 同时打印原始结果。

如果用户只想拿到一个可播放的链接、不要本地文件，就把 `--out` 去掉，
把 stdout 里的 `音频地址` 给他。

## 音色表

```bash
bash "$S/scripts/run.sh" speech speakers
```

| 参数 | 说明 | 语言 |
| --- | --- | --- |
| `zh_ming` | 中文男声（小明） | 中文 |
| `zh_xiaoxiao` | 中文小晓 | 中文 |
| `zh_youyou` | 中文悠悠（默认） | 中文 |
| `en_luka` | 英式发音（Luka） | 英文 |
| `us_annie` | 美式发音（Annie） | 英文 |

**这张表是实测出来的，不是抄文档。** 接口文档里的音色表被压成了一列
（`中文小明zh_ming` 这样名和参数连在一起），名参对应关系在文档里读不出来，
所以只收下真的跑通、拿到 `audioUrl` 的那几个。

**拿不准的音色不要试。** 见下面「两种失败的音色」。

## 两种失败的音色（都实测过）

**1. 拼错的名字会报 500，但记录照样留下。**

`zh_xiaoming` / `en_lukas` / `us_anna` 这类"看起来很像"的名字，接口回
HTTP 500 `server error!`，**可是平台上仍然建出一条记录**，只是 `audioUrl`
永远是 null。

所以**不要用"提交一下看看报不报错"的办法试音色**——报错看得见，垃圾数据
留下来了。CLI 已经在提交前拦住白名单之外的音色。

**2. `us_trump` 会卡住，不会干脆地失败。**

它提交成功、拿到 `taskId`，然后一直停在 `status=2`（执行中），实测 60 秒
仍未到终态。这不是干净的失败，是**卡住**——拿退出码 3 反复轮询也等不到结果。
所以它不在白名单里。

## 查询与重入

合成很快，但并发高时任务会先**排队**（`status=6`），少数情况下会超过 60 秒。

```bash
bash "$S/scripts/run.sh" speech submit "文字" --speaker zh_youyou   # 只提交，秒级返回 taskId
bash "$S/scripts/run.sh" speech poll <taskId> --wait 60             # 短轮询，可反复调用
bash "$S/scripts/run.sh" speech get <taskId>                        # 只查一次
```

- 退出码 `0` → 完成，stdout 有 `音频地址`。
- 退出码 `3` → **还在跑，不是失败**。用**同一个 taskId** 再 poll 一次。
  不要重新 submit（会产生重复记录、重复计费）。
- 退出码 `1` → 真失败，stderr 里有平台给的原因。

## 记录

```bash
bash "$S/scripts/run.sh" speech records
```

每行的 `id` 是**记录 id**，跟 `taskId` **不是一回事**——删记录要用 `id`。

注意这里的 `status` 只有 **4 个取值**（1 已提交 / 2 执行中 / 3 成功 / 4 失败），
跟 `queryTask` 那套 7 态**不是同一套编号**。别把两边的数字对着读。

## 收尾

合成会在平台上留记录。用户想清理时**不要自己动手**——让他走
`/unipus-aigc:guide` 的 `records` / `cleanup`，那里有安全约束
（不带 `--yes` 只列不删，且不得替用户按 `--yes`）。

## 范围之外

用户问到语音合成之外的平台能力（"能不能做 PPT"、"还能干什么"、
"口语评阅支持吗"）时，**不要猜、也不要直接说没有**——让他运行
`/unipus-aigc:guide`，那里有平台应用的完整清单和可做性分档。

## 更深的材料

这条链路是全平台**最标准的一条异步链路**（`task/submit` + `queryTask`），
别的 operation 也走同一套、只是提交字段不同。要照着它实现新应用时，
读 plugin 仓库的 `docs/call-chains.md`。
