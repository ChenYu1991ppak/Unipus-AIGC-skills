# -*- coding: utf-8 -*-
"""图像生成（AI 绘画，operation 10）。

调用链（详见 `内部的接口记录` §5）::

    (1) img/getImgReferenceList  {}                无参，**只读**
          └─> value.data: ImgReference[]          ★ 风格白名单 + 每个风格的尺寸白名单
    (2) task/submit {operation: 10,
                     submitData: {prompt, reversePrompt?, style, size, imgNumber?}}
          └─> taskId
    (3) task/queryTask {taskId} 轮询到 status=3
          └─> value.responseData 是 JSON 字符串，再 loads 一次 -> {imgList: [...]}

    清理：img/queryList {type: 1}    type=1 是 AI 绘画生图历史
          img/delete    {id}        ★ 收的是 **img 表的 id**，不是 taskId

.. warning::
   **``style`` / ``size`` 的合法取值必须以实时接口为准，不能照文档抄。**

   文档点名的六个 ``style``（``manhua`` / ``youhua`` / ``xieshi`` / ``shuicai`` /
   ``gufeng`` / ``sd21``）**确实都在线上白名单里**（实测 11 行），但**在不在白名单
   和能不能出图是两回事**：提交它们平台会**静默改写**成 ``azure-dall-e-3`` /
   ``sd21`` / ``playground``，然后失败或挂住。**白名单是必要不充分条件。**

.. warning::
   **失败也照样建记录，而且报错看不见。** ``taskStatus=4`` 的行在
   ``img/queryList`` 里看得见、``imgList`` 为 ``null``。所以**别用"提交一下看报不报错"
   的办法试 ``style`` / ``size``**——垃圾数据留下了，错误什么也说明不了。
   本模块因此做**两级本地前置校验**（风格不在白名单、尺寸不在该风格的 ``sizeConf``
   里，**发请求前就拒绝**），这是必需项，不是优化项。

   ``size`` 还硬必填：缺了直接 ``code=100 size字段不能为空``，
   **连任务记录都不建**（这一个失败倒是干净的）。
"""

import json

from .constants import Operation
from .errors import AigcError

#: ``img/queryList`` 的 ``type``：1 AI 绘画生图历史 / 2 图片编辑历史图库 /
#: 3 图片编辑历史记录。本模块只用得到 1。
IMAGE_TYPES = {
    1: "AI 绘画生图历史",
    2: "图片编辑历史图库",
    3: "图片编辑历史记录",
}

#: 实测出图的那个风格。**留着当提示，不是白名单**——白名单永远来自
#: :meth:`ImageGenAPI.references`（实时接口）。
KNOWN_GOOD_STYLE = "general_v2.1_L"


class ImgStyle:
    """``img/getImgReferenceList`` 的一行。

    ``style`` 是提交时 ``style`` 字段的值；``sizes`` 是**这个风格自己的**
    合法尺寸（由 ``sizeConf`` 那个 JSON 字符串解析而来）。
    """

    def __init__(self, style, name, style_type, sizes, has_prompt, sort=0):
        self.style = style
        self.name = name
        self.style_type = style_type
        self.sizes = tuple(sizes or ())
        self.has_prompt = bool(has_prompt)
        self.sort = sort or 0

    def __repr__(self):
        return (f"ImgStyle({self.style!r}, {self.name!r}, "
                f"{self.style_type!r}, {len(self.sizes)} sizes)")


class ImageGenAPI:
    """AI 绘画。挂在 ``client.image_gen`` 上。"""

    def __init__(self, client):
        self._c = client
        #: 风格白名单的进程内缓存。**只缓存只读结果**——白名单一天也不会变，
        #: 而每次 :meth:`draw` 都要拿它做前置校验。
        self._refs = None

    # ------------------------------------------------------------------
    # (1) 白名单：img/getImgReferenceList
    # ------------------------------------------------------------------
    def references(self, refresh=False):
        """风格白名单（**实时接口**，无参、只读、无副作用）。

        返回 :class:`ImgStyle` 列表。**这是 ``style`` / ``size`` 的真源**，
        比接口文档准——文档只给了六个风格名，没给尺寸，也没给 ``styleType``。

        .. warning::
           ``value`` 是个 **dict**（``{totalCount, currentPage, pageSize, data}``，
           前三个实测都是 null），数组在 ``value.data`` 里，**不在顶层**。
           照文档或照"顶层有 data"去取会拿到一个 dict，然后 ``.get()`` 报
           ``'str' object has no attribute 'get'``。``sizeConf`` 还是个
           **JSON 字符串**，要再 ``loads`` 一次。
        """
        if self._refs is None or refresh:
            value = self._c.get_value("img/getImgReferenceList", {})
            self._refs = [_style_of(row) for row in _rows(value)]
        return list(self._refs)

    def styles(self):
        """只要 ``style`` 那一列（白名单本身），按 ``sort`` 从大到小。"""
        return [s.style for s in self.references()]

    def sizes_of(self, style):
        """某个风格自己的合法尺寸列表。

        尺寸**跟着风格走，不是一张全局表**——所以本地校验必须是
        "风格在不在白名单" + "尺寸在不在**这个风格**的 ``sizeConf`` 里" 两级。
        """
        return list(self._style(style).sizes)

    def check(self, style, size=None):
        """两级前置校验：风格在不在白名单、尺寸在不在**该风格**的尺寸表里。

        :raises ValueError: 任何一级不过。**这两条都不要发请求去试**——
            这条链路失败也建记录（见模块 docstring）。
        """
        known = self._style(style)
        if size is None:
            return known
        if size not in known.sizes:
            raise ValueError(
                f"尺寸 {size!r} 不是风格 {style!r}（{known.name}）支持的。"
                f"它支持：{'、'.join(known.sizes)}")
        return known

    # ------------------------------------------------------------------
    # (2)(3) 出图
    # ------------------------------------------------------------------
    def draw(self, prompt, style=KNOWN_GOOD_STYLE, size="正方形",
             reverse_prompt=None, img_number=None):
        """提交出图任务，秒级返回 ``taskId``。

        **发请求之前先做两级本地校验**（见 :meth:`check`）——校验不过直接抛
        ``ValueError``，一个字节都不发到平台。这不是洁癖：这条链路失败也照样
        在 ``img/queryList`` 里留一条 ``taskStatus=4`` 的垃圾记录。

        :param style: 必须来自 :meth:`references`。文档点名的六个**在**白名单里
            但会失败/挂住，默认值取实测能出图的 ``general_v2.1_L``。
        :param size: 必须在这个风格的 ``sizeConf`` 里。硬必填。
        :param reverse_prompt: 负向提示词。可传、会被记录，**不影响成败**。
        :param img_number: 出图张数。同上（``2`` 确实回两张）。
        """
        self.check(style, size)
        submit = {"prompt": prompt, "style": style, "size": size}
        if reverse_prompt is not None:
            submit["reversePrompt"] = reverse_prompt
        if img_number is not None:
            submit["imgNumber"] = img_number
        return self._c.submit_task(Operation.AiDraw, submit)

    def get(self, task_id):
        """查一次。未完成返回 ``None``，失败抛 ``TaskFailed``。"""
        return self._c.parse_task_result(self._c.query_task(task_id))

    def wait(self, task_id, *, interval=3, timeout=180):
        """轮询到出图。

        拿到的是 ``{"imgList": [...]}``（``responseData`` 解一层之后）。
        """
        data = self._c.wait_task(task_id, interval=interval, timeout=timeout)
        return images_of(data)

    def poll(self, task_id, *, interval=3, timeout=60):
        """短轮询：没出图就抛 ``StillRunning``（CLI 映射成退出码 3）。"""
        from .errors import StillRunning, TaskTimeout
        try:
            return self.wait(task_id, interval=interval, timeout=timeout)
        except TaskTimeout as e:
            raise StillRunning(
                f"绘画任务 {task_id} 仍在处理中，{timeout}s 内未出图。"
                f"稍后用同一个 taskId 再 poll 一次。",
                path="task/queryTask", payload=e.payload) from e

    def draw_and_wait(self, prompt, style=KNOWN_GOOD_STYLE, size="正方形",
                      *, interval=3, timeout=180, **kw):
        """提交 + 等出图，一次拿到 ``imgList``。"""
        task_id = self.draw(prompt, style, size, **kw)
        return {"taskId": task_id, "imgList": self.wait(task_id, interval=interval,
                                                       timeout=timeout)}

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def records(self, type_=1, page=1, size=20):
        """``img/queryList``：历史记录。

        ``pageNum`` / ``pageSize`` **是必填的**（实测：只发 ``type`` 报
        ``code=100 pageNum 不能为空``），所以这里一直带着，跟
        ``speech/urlList``、``translate/list`` 同一套分页约定。

        ``type=1`` 是 AI 绘画生图历史（``2`` 图片编辑历史图库 /
        ``3`` 图片编辑历史记录）。每行的 ``id`` 是 **img 表的 id**，
        删记录要用它，不是 ``taskId``。
        """
        return self._c.get_value(
            "img/queryList",
            {"type": type_, "pageNum": page, "pageSize": size})

    def delete(self, *img_ids):
        """``img/delete``，按 **img 表的 id**（不是 ``taskId``）。

        用错 id 的后果是**删不掉还看不出来**——拿 ``taskId`` 去删，
        接口不会报错，那条记录还在。所以 id 要从 :meth:`records` 的
        ``id`` 字段取。
        """
        ids = list(img_ids)
        if not ids:
            return None
        return self._c.get_value("img/delete", {"id": ids[0]})

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def style_table(refs):
        """白名单表格：``style`` / 名字 / 模型类型 / 尺寸数 / 尺寸。"""
        rows = [f"{'style':<16}{'名字':<14}{'类型':<8}{'尺寸':<4}尺寸表",
                "-" * 78]
        for s in sorted(refs, key=lambda x: x.sort, reverse=True):
            rows.append(f"{s.style:<16}{s.name:<12}{s.style_type:<8}"
                        f"{len(s.sizes):<6}{'、'.join(s.sizes)}")
        return "\n".join(rows)

    @staticmethod
    def format_result(result):
        if isinstance(result, list):
            images = result
            task_id = None
        else:
            images = (result or {}).get("imgList") or []
            task_id = (result or {}).get("taskId")
        lines = [f"taskId   : {task_id}   共 {len(images)} 张"]
        for i, img in enumerate(images, 1):
            if isinstance(img, dict):
                lines.append(f"  [{i}] id={img.get('id')}  {img.get('url') or img}")
            else:
                lines.append(f"  [{i}] {img}")
        lines.append("注意：删记录用每行的 id（img 表的 id），不是 taskId。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _style(self, style):
        refs = self.references()
        for s in refs:
            if s.style == style:
                return s
        raise ValueError(
            f"风格 {style!r} 不在白名单里。白名单**以实时接口为准**："
            f"{'、'.join(s.style for s in refs)}"
            f"（跑 `image styles` 看中文名和尺寸表）")


# ----------------------------------------------------------------------
# 模块级小工具
# ----------------------------------------------------------------------
def _rows(value):
    """从 ``getImgReferenceList`` 的 ``value`` 里取出 ``ImgReference[]``。

    ``value`` 实测是 ``{totalCount, currentPage, pageSize, data}`` 这个 dict，
    数组在 ``data`` 里。**顶层没有 ``data``**——早先的探针就是在这里栽的：
    它写 ``body.get("data") or body.get("value")``，于是拿到那个 4 键 dict，
    ``len()`` 打出 ``4``、迭代出的是字符串 key，再 ``.get()`` 就炸了。
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            return data
    return []


def _style_of(row):
    """``ImgReference`` 的一行 -> :class:`ImgStyle`。``sizeConf`` 要再 ``loads``。"""
    conf = row.get("sizeConf")
    if isinstance(conf, str):
        try:
            conf = json.loads(conf)
        except ValueError:
            conf = []
    if not isinstance(conf, (list, tuple)):
        conf = []
    return ImgStyle(
        style=row.get("style") or "",
        name=row.get("name") or "",
        style_type=row.get("styleType") or "",
        sizes=[s for s in conf if isinstance(s, str)],
        has_prompt=row.get("prompt"),
        sort=row.get("sort"),
    )


def images_of(data):
    """从 op10 的 ``responseData`` 里取出 ``imgList``。

    ``responseData`` 解一层之后应该是 ``{"imgList": [...]}``；平台改行为时
    也可能直接把列表放在 ``responseData`` 上，两种都认。

    **拿不到就抛**——静默返回空列表会把"结构变了"伪装成"没出图"，
    而"没出图"在这条链路上通常意味着失败记录已经留下了。
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        raise AigcError("绘画结果不是预期结构", path="op10", payload=data)
    if "imgList" in data:
        return data["imgList"] or []
    raise AigcError("绘画结果里没有 imgList", path="op10", payload=data)
