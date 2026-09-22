---
name: image-gen
description: >-
  Generate images through the Unipus AIGC platform (AI 绘画 / 文生图) — pick a
  style from the live style whitelist, pick a size that style actually supports,
  submit the prompt, and collect the resulting image URL. Use when the user asks
  for 图片生成 / AI 绘画 / AI 绘图 / 文生图 / 根据描述生成图片 / 给文章配图 /
  画一张图 / 生成插画 / 生成配图. Also covers listing past generation records and
  deleting them.
  通过 Unipus AIGC 平台生成图片（AI 绘画），风格表必须现取，不能照文档抄。
---

# Unipus AIGC · 图像生成（AI 绘画）

一条链路：**取风格白名单 → 选风格+尺寸 → 出图 → 拿图片地址**。

## 先问清楚两件事

1. **画什么** —— 提示词。用户只给了一个词时，**先把它扩成一句可用的描述**
   再发（这条链路的 `prompt` 是纯文本，没有模板兜底）。
2. **什么风格、什么比例** —— 见下面的表。用户没指定就用默认
   `general_v2.1_L` + `正方形`，**并告诉他你选了什么**。

## 凭证（token）

**先直接跑，别先问凭证。** 续期是自动的——只要之前配过账号密码，
token 失效时**会自己换新**，你和用户都不需要做任何事。

只有命令**真的失败**之后才需要处理。先看一眼现状：

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

## ⚠️ 这一条最容易踩：风格表要现取，不能照文档抄

平台接口文档点名的六个风格（`manhua` / `youhua` / `xieshi` / `shuicai` /
`gufeng` / `sd21`）**都在**线上白名单里，但**在白名单里 ≠ 能出图**：
提交它们，平台会**静默改写**成 `Dall-E-3` / `通用模型一` / `通用模型二`，
然后**失败或挂住**。

实测真能出图的是 **`general_v2.1_L`（丹青模型）**——它的尺寸表也最宽。

所以：

```bash
bash "$S/scripts/run.sh" image styles        # ← 风格和尺寸的真源，永远先跑这个
```

| `style` | 名字 | 类型 | 尺寸数 |
| --- | --- | --- | --- |
| `general_v2.1_L` | 丹青模型 | 通用模型 | **7**（正方形 / 3:4 / 4:3 / **2:3** / **3:2** / 9:16 / 16:9） |
| `nova-canvas` | Nova-Canvas | 通用模型 | 5 |
| `gufeng` | 中国古风 | 风格模型 | 5 |
| `manhua` | 漫画风 | 风格模型 | 5 |
| `shuicai` | 水彩风 | 风格模型 | 5 |
| `youhua` | 油画风 | 风格模型 | 5 |
| `xieshi` | 写实风 | 风格模型 | 5 |
| `playground` | 通用模型二 | 通用模型 | 5 |
| `sd21` | 通用模型一 | 通用模型 | 5 |
| `luxiaobei` | 鹿小北 | 专属模型 | 5 |
| `azure-dall-e-3` | Dall-E-3 | 通用模型 | 3 |

> `nova-canvas` 和 `luxiaobei` 是**从没试过**的两个。表里有它，只说明它是合法入参，
> 不说明它出得了图。**本轮唯一实测出图的是 `general_v2.1_L`**；历史记录里还有一条
> 更早的 `通用风格` 成功行，但那个值**不在今天的白名单里**，别拿它当入参。

**尺寸跟着风格走，不是一张全局表。** `size` 还硬必填。

## 两级本地校验是硬要求，不是洁癖

**这条链路失败也照样建记录**：`taskStatus=4` 的行在 `image records` 里看得见、
`imgList` 为 `null`。报错看不见，垃圾数据留下了。

所以**别用"提交一下看报不报错"试风格/尺寸**。`image sizes` 和 `image draw` 都会在
**发请求之前**校验：风格不在白名单、或尺寸不在**该风格**的尺寸表里，**直接退出码 2，
一个字节都不发**。

## 出图

```bash
bash "$S/scripts/run.sh" image draw "一本摊开的英语教材放在木桌上，旁边一杯咖啡" \
    --style general_v2.1_L --size 正方形
```

默认 `--wait 180`。出图不是秒级，但比出题快。超时是退出码 **3，不是失败**——
用同一个 `taskId` 再 poll：

```bash
bash "$S/scripts/run.sh" image poll <taskId>
```

stdout 给图片 URL。**把 URL 原样给用户**（那是七牛上的 png，可直接打开）。

可选：`--reverse-prompt`（负向提示词）、`--img-number`（张数）。
两个都**会被记录、都不影响成败**。

## 记录与清理

```bash
bash "$S/scripts/run.sh" image records                # type=1 生图历史
bash "$S/scripts/run.sh" image delete <id>            # 只列不删
bash "$S/scripts/run.sh" image delete --failed        # 列出 taskStatus=4 的垃圾记录
```

**`image delete` 收的是 `id`（img 表的 id），不是 `taskId`。**
拿 `taskId` 去删，接口**不报错、记录还在**——删完请用 `image records` 复核一次。

**不带 `--yes` 只列不删。** 确认无误后**由用户自己**加 `--yes` 重跑——
不要替他按，也不要引导他"直接加 `--yes` 就行"。

```bash
bash "$S/scripts/run.sh" image records --size 5      # 拿 id 用这个
```

## 范围之外

用户问到平台别的能力（"能编辑图片吗"、"能抠图吗"、"还能干什么"）时，
**不要猜、也不要直接说没有**——让他运行 `/unipus-aigc:guide`，那里有平台应用的
完整清单和可做性分档。

> 图片**编辑**（高清修复 / 抠图 / 图像融合，`operation` 19/21/44/66）走的是
> 另一组接口，**本 skill 不覆盖**。

## 更深的材料

这一条是"文档有值但实测不通"的第二个样本（第一个是智能出题）。接口记录在 plugin
仓库的（未随仓库发布）内部的接口记录 §5，风格白名单的实测数据在 §5 那条反例里。
