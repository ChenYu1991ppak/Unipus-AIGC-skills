# -*- coding: utf-8 -*-
"""文章写作 / 文本生成。

平台上**没有**一个叫「文本生成」的应用（`scene-map/get` 的 25 个里没有，
`menu_all.json` 里也没有；`grep` 全库只有 menuId 812「根据文本生成阅读理解题」
带这四个字）。所以这一支按**能力**落地：`55226315` 的 `article/*` 一族
+ 前端实际在用的 `lm/*` 一族。

.. warning::
   **`article/*` 里那四个"写侧"端点是死的。** 文档把
   `aiTextOperation`（续写/扩写/优化）、`aiOperation`（一级大纲/二级大纲/正文）、
   `aiOptimizeArticle`（整篇优化）的返回表都写得很具体
   （`content` 是 string 或 `TxtStruct[]`），**实测三个的 `value.content` 恒为
   `null`**，而且**不看入参**：

   * 给真 `articleId` 是 `{"content": null}`；
   * 给 `articleId="1"`（**根本不存在的文章**）`aiTextOperation` 回**逐字节相同**的
     `{"content": null}`——跟 op50 是同一类"给定的空壳回执"；
   * 把正文用 `updateArticle` 真写进文章之后再打，**还是** `{"content": null}`。

   所以本模块**不实现**它们（实现了只会给用户一个永远为空的字段）。
   前端的产物也印证了这点：`uaigc_index.js` 里搜得到 `article/aiTitle`、
   `article/insertArticle`、`article/delete`……**搜不到**
   `aiTextOperation` / `aiOperation` / `aiOptimizeArticle`——**前端根本没用**。

.. warning::
   **真正在用的是另一族 `lm/*`，而且它是 SSE 流式。** 前端调用点
   （`uaigc_index.js`）::

       // 续写 / 改写 / 通用续写 / 大纲
       "/api/aigc/lm/rewrite/content"          {lmId, content, rewriteMethodId,
                                                subType, type, fullContent,
                                                customPrompt, referenceDataUrl}
       "/api/aigc/lm/content/continueWrite"    {lmId, customPrompt, startContent,
                                                endContent, referenceDataUrl}
       "/api/aigc/lm/content/commonContinueWrite"  {subType, data:{before, after}}
       "/api/aigc/lm/generate/outline"         {lmId, fullContent}

   * `generate/outline` 是**普通 JSON**（回 `value` = markdown 文本）；
   * 另外三个是 **SSE**（`content-type: text/event-stream`，帧形如
     `data:{"choices":[{"delta":{"content":"…"}}]}`，`[DONE]` 收尾）。

   **这不是 socket.io 推送**——是普通的 SSE over HTTP。早先"文章写作 op1–8
   要消费 Socket.IO 增量推送"的猜测**不成立**，实际是 SSE，客户端用起来简单得多。

.. warning::
   SSE 的响应头 **不带 charset**。requests 对 `text/*` 的默认编码是
   ISO-8859-1，不把 `resp.encoding` 钉成 utf-8，中文会整段变成 `ä¸æ`——
   跟 v2 RAG 流式那个坑是同一个（见 :meth:`RagV2API._iter_stream`）。

文档出处：`55226315-AIGC接口文档.md` 的 `article/*` 一节（L323–L943、
L1097–L1330）。`lm/*` **不在文档里**，是前端产物里挖出来的。
"""

import json
import time

from . import config
from .errors import AigcError

#: `TxtStruct.type` 的三个取值（文档的 `TxtStruct文本结构` 表）。
TXT_TYPE = {"H1": "一级大纲", "H2": "二级大纲", "T": "正文"}

#: `article/aiTitle` 的 `aiTitleType`。
TITLE_TYPES = {
    1: "根据话题方向推荐标题",
    2: "根据旧标题换新标题",
    3: "根据内容推荐新标题",
}

#: `article/updateArticle` 里 `subType` 的实测取值（`queryArticleDetail` 回的那列）。
SUB_TYPES = {2: "公众号文案"}


class ArticleAPI:
    """文章写作。挂在 ``client.article`` 上。"""

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # article/* 基本 CRUD（这一族是活的）
    # ------------------------------------------------------------------
    def create(self, submit_time=None, **fields):
        """`article/insertArticle`：新增文章，返回 ``articleId``。

        **唯一硬必填的字段是 ``submitTime``**（long，13 位毫秒时间戳，客户端自己
        生成）——文档字段表里其余全是 ``false``。所以 ``articleId`` 我们自己造得
        出来，不用先去页面上建一篇。

        .. warning::
           `article/delete` **存在**（这条链路能删干净，跟出题那条的 `rmId` 不同），
           但删之前先把 ``articleId`` 记下来。
        """
        payload = {"submitTime": submit_time or int(time.time() * 1000)}
        payload.update(fields)
        value = self._c.get_value("article/insertArticle", payload) or {}
        article_id = value.get("articleId") if isinstance(value, dict) else value
        if not article_id:
            raise AigcError("建文章成功但没拿到 articleId",
                            path="article/insertArticle", payload=value)
        return article_id

    def detail(self, article_id):
        """`article/queryArticleDetail`。行里有 ``content`` / ``currentVersion`` /
        ``versionSize`` / ``subType`` 等。

        实测 ``articleId`` 传字符串就行（文档标 long，服务端两种都吃）。
        """
        return self._c.get_value("article/queryArticleDetail",
                                 {"articleId": article_id})

    def update(self, article_id, title, *, content=None, word_count=None,
               image=None, submit_time=None):
        """`article/updateArticle`。``articleId`` / ``title`` / ``submitTime``
        三个必填（文档明说"保存文章会校验 `submitTime` 字段"）。

        **只发显式给出的字段**——不搞"两个都发"。
        """
        payload = {"articleId": article_id, "title": title,
                   "submitTime": submit_time or int(time.time() * 1000)}
        if content is not None:
            payload["content"] = content
        if word_count is not None:
            payload["wordCount"] = word_count
        if image is not None:
            payload["image"] = image
        return self._c.get_value("article/updateArticle", payload)

    def delete(self, article_id):
        """`article/delete`：**按 `userId` + `articleId` 删掉该文章的**所有版本**。

        这条链路跟出题那条不一样——**能删干净**。
        """
        return self._c.get_value("article/delete", {"articleId": article_id})

    def articles(self, template_type=0, page_num=1, page_size=20, **extra):
        """`article/getArticleList`。

        .. warning::
           ``templateType`` **是必填的**（实测：只发分页参数报
           ``code=100 templateType不能为空``，文档标的是 false）。
           默认 ``0``（推文；文档：``0`` 推文 / ``1`` 教学模版 / ``2`` 营销模版 /
           ``3`` 办公模版）。
        """
        payload = {"templateType": template_type,
                   "pageNum": page_num, "pageSize": page_size}
        payload.update(extra)
        value = self._c.get_value("article/getArticleList", payload) or {}
        if isinstance(value, list):
            return value
        return value.get("data") or value.get("list") or []

    # ------------------------------------------------------------------
    # 标题（活的）
    # ------------------------------------------------------------------
    def ai_title(self, title_type=1, *, article_id=None, title=None, content=None,
                 template_type=None, title_style=None):
        """`article/aiTitle`：生成标题，返回 ``titleList``（**10 条**，实测）。

        :param title_type: 见 :data:`TITLE_TYPES`。
            ``1`` 按话题方向推荐 / ``2`` 换旧标题（要 ``title``）/
            ``3`` 按内容推荐（要 ``content``，``TxtStruct[]``）。
        :param article_id: **建议一定要给。** 文档说 ``aiTitleType`` 为 1/2/3
            时都传，**它是对的**——实测不给的话接口**照样回满 10 条标题、不报错**，
            但跟你的文章毫无关系（同一篇文章，不给 id 时回的是
            「如何让生活更高效」这类泛标题）。**不报错 ≠ 结果对**，这是本链路
            最容易吃亏的一处。
        :param content: ``[{ "type": "T", "txt": "…" }]``，``title_type=3`` 用。

        三种 ``title_type``（配 ``article_id``）实测各真跑一次，都回满 10 条标题。
        """
        payload = {"aiTitleType": title_type}
        if article_id is not None:
            payload["articleId"] = article_id
        if title is not None:
            payload["title"] = title
        if content is not None:
            payload["content"] = content
        if template_type is not None:
            payload["templateType"] = template_type
        if title_style is not None:
            payload["titleType"] = title_style
        value = self._c.get_value("article/aiTitle", payload) or {}
        return value.get("titleList") or []

    # ------------------------------------------------------------------
    # lm/generate/outline —— 普通 JSON
    # ------------------------------------------------------------------
    def outline(self, article_id, full_content):
        """`lm/generate/outline`：按正文生成大纲，返回 **markdown 文本**。

        前端形态是 ``{lmId, fullContent}``（`lmId` 传的就是 `articleId`，
        字符串/数字都吃）。``fullContent`` 不给报 ``code=100 文本内容不能为空``。

        **这个是普通 JSON，不是流式**——跟下面三个不一样。
        """
        value = self._c.get_value("lm/generate/outline",
                                  {"lmId": article_id, "fullContent": full_content})
        if isinstance(value, dict):
            return value.get("value") or value.get("content") or ""
        return value or ""

    # ------------------------------------------------------------------
    # lm/* 的 SSE 流式三兄弟
    # ------------------------------------------------------------------
    def continue_write(self, article_id, start_content, end_content,
                       *, custom_prompt=None, reference_data_url=None, timeout=None):
        """`lm/content/continueWrite`（SSE）：在 ``startContent`` 和
        ``endContent`` 之间续写。返回**逐块 yield 文本增量**的生成器。

        :return: generator of ``str``（每个增量）。要拼接后的全文用
            :meth:`continue_write_text`。
        """
        payload = {"lmId": article_id, "startContent": start_content,
                   "endContent": end_content}
        if custom_prompt is not None:
            payload["customPrompt"] = custom_prompt
        if reference_data_url is not None:
            payload["referenceDataUrl"] = reference_data_url
        return self._sse("lm/content/continueWrite", payload, timeout=timeout)

    def common_continue(self, sub_type=15000, *, before=None, after=None,
                        data=None, timeout=None):
        """`lm/content/commonContinueWrite`（SSE）：**不依赖文章的通用续写**。

        前端两种形态都用过：``{subType, data:{before, after}}``（富文本编辑器里
        取光标前后的文本），以及 ``{subType:15000, data:{before, after}}``。
        这里两种都收。

        实测 `subType=15000` 能出文本。
        """
        payload = {"subType": sub_type, "data": data or {}}
        if before is not None:
            payload["data"].setdefault("before", before)
        if after is not None:
            payload["data"].setdefault("after", after)
        return self._sse("lm/content/commonContinueWrite", payload, timeout=timeout)

    def rewrite(self, article_id, content, *, rewrite_method_id=1, sub_type=15000,
                type_=1, full_content=None, custom_prompt=None,
                reference_data_url=None, timeout=None):
        """`lm/rewrite/content`（SSE）：按方法改写一段内容。

        :param content: 要改写的那一段。
        :param full_content: **建议给**——不给报 ``code=100 文章内容不能为空``
            （实测）。前端就是 `fullContent` 和 `content` 一起给的。
        :param rewrite_method_id: 改写方法 id（前端叫 ``rewriteMethodId``）。
            实测 ``1`` / ``2`` 都能出文本；合法取值表**没拿到**，别硬编全套。
        """
        payload = {"lmId": article_id, "content": content,
                   "rewriteMethodId": rewrite_method_id,
                   "subType": sub_type, "type": type_}
        if full_content is not None:
            payload["fullContent"] = full_content
        if custom_prompt is not None:
            payload["customPrompt"] = custom_prompt
        if reference_data_url is not None:
            payload["referenceDataUrl"] = reference_data_url
        return self._sse("lm/rewrite/content", payload, timeout=timeout)

    # --- 拼好的便利版（要全文、不要增量）---
    def continue_write_text(self, *a, **kw):
        return "".join(self.continue_write(*a, **kw))

    def common_continue_text(self, *a, **kw):
        return "".join(self.common_continue(*a, **kw))

    def rewrite_text(self, *a, **kw):
        return "".join(self.rewrite(*a, **kw))

    # ------------------------------------------------------------------
    # 内部：SSE
    # ------------------------------------------------------------------
    def _sse(self, path, payload, *, timeout=None):
        """POST 一条 SSE 流，逐帧 yield 文本增量。

        帧格式（实测）::

            data:{"choices":[],"created":0,"id":"null","model":"","object":""}
            data:{"choices":[{"delta":{"content":"1"},"index":0}],…,"object":"chat.completion.chunk"}
            data:[DONE]

        第一条 ``choices`` 是空数组——**要防着**，别直接下标。
        """
        url = f"{config.API_BASE}/api/aigc/{path}"
        # 前端只发 Content-Type + token（不发 Accept）；我们多带两个无害的头。
        headers = {"Content-Type": "application/json", "token": self._c.token,
                   "Authorization": f"Bearer {self._c.token}",
                   "source": config.SOURCE}
        resp = self._c.session.post(url, data=json.dumps(payload, ensure_ascii=False),
                                    headers=headers, stream=True,
                                    timeout=timeout or self._c.timeout * 4)
        if resp.status_code != 200:
            raise AigcError(f"HTTP {resp.status_code}", code=resp.status_code,
                            path=path, payload=resp.text[:400])
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "stream" not in ctype:
            # 不是流——说明参数不对，服务端回的是普通 JSON 错误体。
            resp.encoding = "utf-8"
            body = resp.text[:500]
            try:
                obj = json.loads(body)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                raise AigcError(obj.get("msg") or "接口返回失败",
                                code=obj.get("code"), path=path, payload=obj)
            raise AigcError("期望 SSE 流，拿到的是普通响应", path=path,
                            payload=body)
        # ⚠️ `text/event-stream` **不带 charset**，不钉成 utf-8 中文会变成 ä¸æ。
        resp.encoding = "utf-8"
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                body = raw[5:].strip()
                if body == "[DONE]":
                    return
                try:
                    frame = json.loads(body)
                except ValueError:
                    continue
                choices = frame.get("choices") or []
                if not choices:
                    continue        # 首帧就是空 choices，跳过
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
        finally:
            resp.close()


def txt_struct(txt, type_="T"):
    """拼一个 ``TxtStruct``：``{"type": "H1"|"H2"|"T", "txt": "…"}``。"""
    return {"type": type_, "txt": txt}


def outline_markdown(text):
    """把 :meth:`ArticleAPI.outline` 回的 markdown 原样返回（留个口子给调用方
    换渲染）。"""
    return text or ""
