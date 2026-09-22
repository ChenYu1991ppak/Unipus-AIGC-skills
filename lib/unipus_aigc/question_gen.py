# -*- coding: utf-8 -*-
"""出题：阅读材料 → 生成题目 → 采纳 → 取题面。

这条链路**不在** ``64754844``（异步任务文档）里，而在 ``55226315`` 的
``rm/*`` + ``ques/*`` 一节。两条通道都用得上：

======================================  ==========================================
操作                                   通道
======================================  ==========================================
建/查/改阅读材料 ``rm/*``              普通 HTTP（``client.post``）
出题 ``ques/generation``（§2.1）       普通 HTTP，**同步**返回题面但**不落库**
出题 op12（``task/submit``）            **异步任务**，轮询到 ``status=3`` 才有结果
其余 ``ques/*``（采纳/列表/题面）      普通 HTTP（``client.post``）
======================================  ==========================================

.. warning::
   两条出题路径的区别**是实测出来的，不是文档写的**：

   * ``ques/generation``（§2.1）即时回题面，**但服务端不留记录**——
     紧接着 ``ques/generationQuesList`` 仍报 ``code=1001 没有生成题目记录``，
     ``rm/list`` 的 ``generateCount`` 也还是 ``0``。**它没有 ``quesId``**，
     所以生成的题**无法采纳**。
   * **op12 才是能用的那条**：``task/submit`` + 轮询，``responseData.content``
     是一个 JSON 字符串，解出来每题带 ``quesId``——这正是 ``ques/accept``
     要的 ``questionId``。跑完 ``generateCount`` 会加一。

   文档把 op12 记成「差一个拿不到的 ``rmId``」。**``rmId`` 自己造得出来**：
   §1.1 ``rm/create`` 的 ``value`` 里就回一个。见 :meth:`create_material`。

.. warning::
   ``rm/delete`` **不存在**。全 4518 行扫描确认 ``rm`` 只有
   ``create`` / ``detail`` / ``update`` / ``list`` 四个端点；出题侧唯一的删除是
   ``ques/delete``（按 ``questionId``，**不按 ``rmId``**）。
   所以**阅读材料建了就删不掉**——复用一条长期的，别每次建新的。
   （对照：绘画记录走 ``img/delete``，能删干净。）

实测记录见 `内部的接口记录`。
"""

import json

from .constants import Operation, TaskStatus
from .errors import AigcError

# ----------------------------------------------------------------------
# 枚举（照 §1.1 / §1.4 / §2.1 的字段表抄，不要和别的编号混）
# ----------------------------------------------------------------------
EDUCATION = {
    1: "小学",
    2: "初中",
    3: "高中",
    4: "职教",
    5: "本科",
    6: "研究生",
    7: "其他",
}

#: ``rm/create`` 的 ``subType``——**注意 ``5`` 与 menuId 780「智能出题」的
#: ``subType: 5`` 逐字对上**，两者是同一套编号。
RM_SUBTYPE = {
    5: "智能出题",
    24: "生成单选题",
    25: "生成多选题",
    26: "生成判断题",
    27: "生成问答题",
    28: "生成排序题",
}

#: 出题策略 ``code``（``questionPloyList`` 里那个）。
#:
#: .. warning::
#:    这套 ``code`` 与 op37（智能出排序题）的 ``code``（1014 内容推理 /
#:    1024 单词释义 / 1034 段落大意）**不是一套编号**，别互相套用。
PLOY_CODES = {
    1010: "事实细节题-选择题",
    1011: "事实细节题-判断正误题",
    1012: "事实细节题-简答题",
    1020: "推断题-选择题",
    1021: "推断题-判断正误题",
    1022: "推断题-简答题",
    1030: "主旨大意题-选择题",
    1031: "主旨大意题-判断正误题",
    1032: "主旨大意题-简答题",
}


class QuestionGenAPI:
    """出题业务封装。挂在 ``client.question_gen`` 上。"""

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # §1 阅读材料 rm/*
    # ------------------------------------------------------------------
    def create_material(self, content, education=5, sub_type=5, **extra):
        """§1.1 建阅读材料，返回 ``rmId``。

        文档字段表（L2824 起）标 ``true`` 的只有三个：
        ``education`` / ``content`` / ``subType``，实测只发这三个就能建。

        .. warning::
           **返回的字段名实测是 ``rmId``，不是文档写的 ``id``。**
           （``55226315`` 那份导出把字段名也拆行渲染过，同一类毛病。）
           本方法两个名字都认。返回的**是阅读材料 id**，op12 直接吃。

        .. warning::
           ``rm/delete`` 不存在，**这条材料建完就删不掉了**。
           复用一条长期的，别每次建新的。

        :param education: 教育阶段，1–7，见 :data:`EDUCATION`
        :param sub_type: 见 :data:`RM_SUBTYPE`，出题场景传 ``5``
        :param extra: 其余可选字段原样透传（``wordCount`` / ``tag`` /
            ``quesType`` / ``quesDescription`` / ``direction`` 等）
        """
        payload = {"education": education, "content": content, "subType": sub_type}
        payload.update(extra)
        value = self._c.get_value("rm/create", payload) or {}
        rm_id = value.get("rmId") or value.get("id")
        if not rm_id:
            raise AigcError("建阅读材料成功但没拿到 rmId", path="rm/create", payload=value)
        return rm_id

    def material(self, rm_id):
        """§1.2 阅读材料详情。"""
        return self._c.get_value("rm/detail", {"rmId": rm_id})

    def update_material(self, rm_id, **fields):
        """§1.3 改阅读材料。"""
        payload = {"rmId": rm_id}
        payload.update(fields)
        return self._c.post("rm/update", payload)

    def materials(self, page_num=1, page_size=20, tag=None):
        """§1.4 阅读材料列表。

        行里的 ``generateCount`` 是**出题次数**，``acceptQuestionPloyList``
        是每个策略的 ``{code, count, acceptCount}``。
        **只按策略出题（``ques/generation``）不会让 ``generateCount`` 动**；
        走 op12 才会。可以用这一点在本机判断刚才那次出题到底落库没有。
        """
        payload = {"pageNum": page_num, "pageSize": page_size}
        if tag is not None:
            payload["tag"] = tag
        value = self._c.get_value("rm/list", payload) or {}
        return value.get("data") or []

    # ------------------------------------------------------------------
    # §2.1 即时出题（同步、不落库、拿不到 quesId）
    # ------------------------------------------------------------------
    def preview(self, rm_id, ploys, level=0, remark=None):
        """§2.1 ``ques/generation``：**同步**出题，题面直接在响应里。

        .. warning::
           **这一路不落库、也没有 ``quesId``，生成的题无法采纳。** 实测：
           紧接着查 ``generationQuesList`` 仍报 ``1001 没有生成题目记录``，
           ``rm/list`` 的 ``generateCount`` 也不动。要真出题用 :meth:`generate`。

           留着它是因为它**不留任何残留**——想试策略/难度组合时用它比用
           op12 干净（op12 出了题就删不掉了）。

        文档说 ``level`` 是唯一必填（``rmId`` / ``questionPloyList`` 都标
        ``false``）。**没验过缺 ``rmId`` 会怎样**——别据此推断。

        :param ploys: ``[{"code": 1010, "count": 3}, ...]``，见 :data:`PLOY_CODES`
        :return: ``questionList``（``[{ques, text, code, json}]``，``json`` 是题目结构的 JSON 字符串）
        """
        payload = {"rmId": rm_id, "questionPloyList": _ploys(ploys), "level": level}
        if remark is not None:
            payload["remark"] = remark
        value = self._c.get_value("ques/generation", payload) or {}
        return value.get("questionList") or []

    # ------------------------------------------------------------------
    # op12 出题（异步、落库、有 quesId）
    # ------------------------------------------------------------------
    def generate(self, rm_id, ploys, level=0, remark=None, *, interval=3, timeout=180):
        """op12 智能出题：**真出题**那条路。

        走 ``task/submit`` + 轮询（不是 §2.1 那条同步路）。``responseData``
        是 ``{"content": "<JSON 字符串>"}``，再 ``loads`` 一次才是
        ``questionList``，**每题带 ``quesId``**。

        :return: ``questionList``——每项 ``{accept, code, ques, quesId, rmId, txt}``。
            ``quesId`` 就是 :meth:`accept` / :meth:`cancel_accept` /
            :meth:`delete` 要的 ``questionId``。

        .. warning::
           **出了题就删不干净**：``ques/delete`` 只删题目，
           那条阅读材料留在 ``rm/list`` 里（``generateCount`` 也是累加的）。
        """
        submit_data = {"rmId": rm_id, "questionPloyList": _ploys(ploys), "level": level}
        if remark is not None:
            submit_data["remark"] = remark
        task_id = self._c.submit_task(Operation.QuestionGen, submit_data)
        data = self._c.wait_task(task_id, interval=interval, timeout=timeout)
        return questions_of(data), task_id

    # ------------------------------------------------------------------
    # §2.2–§2.9 记录与采纳
    # ------------------------------------------------------------------
    def records(self, rm_id):
        """§2.2 出题历史列表。行 = ``{pid, acceptQuestionPloyList, created}``。

        ``pid`` 是**出题记录 id**，可以拿去 :meth:`record`。

        .. warning::
           材料建好但还没出过题时，这里是 ``code=1001 没有生成题目记录``
           ——**是个正常的空态报错，不是失败**。本方法把它当空列表返回，
           其余错误照抛。
        """
        return self._soft_list("ques/generationQuesList", {"rmId": rm_id},
                               "没有生成题目记录")

    def record(self, pid):
        """§2.7 按出题记录 id（``pid``）查那一次出的题。"""
        return self._soft_list("ques/getQuestionRecord", {"pid": pid},
                               "没有生成题目记录")

    def last_record(self, rm_id):
        """§2.6 最新一次出题记录。字段同 :meth:`records` 的行。"""
        return self._soft_list("ques/getLastQuestionRecord", {"rmId": rm_id},
                               "没有最新的出题记录")

    def accepted(self, rm_id):
        """§2.5 采纳题目列表。行 = ``{quesId, quesCode, ques, text, accept, quesText}``。

        **没采纳过时返回空列表**（实测 ``value: []``），不是报错。
        """
        value = self._c.get_value("ques/acceptQuesList", {"rmId": rm_id})
        return value if isinstance(value, list) else []

    def questions_json(self, rm_id):
        """§2.9 生成题目 json 结构。返回**已 ``loads`` 过的 dict**。

        实测这个结构是「阅读材料 + 采纳的题」拼成一棵树：
        顶层 ``contents`` 里放的是原文（``type: 4``），``children`` 里是采纳的题。

        .. warning::
           **一条都没采纳时报 ``code=1001 没有采纳题目``**——
           它要的是「采纳后的题面」，不是原始出题结果。
        """
        value = self._c.get_value("ques/generationQuesJson", {"rmId": rm_id})
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def accept(self, question_id):
        """§2.3 采纳题目。返回 ``True``。

        ``question_id`` 是 **``quesId``**（来自 :meth:`generate` 的结果或
        :meth:`last_record`），**不是** ``code``、**也不是** ``rmId``——
        拿错会得到 ``code=1001 采纳题目不存在``（实测）。
        """
        return self._c.get_value("ques/accept", {"questionId": question_id})

    def cancel_accept(self, question_id):
        """§2.8 取消采纳。返回 ``True``。"""
        return self._c.get_value("ques/cancelAccept", {"questionId": question_id})

    def delete(self, question_id):
        """§2.4 删除题目。返回 ``True``。

        **出题侧唯一的删除手段，而且按 ``questionId``，不是按 ``rmId``。**
        删掉的是题目本身，那条阅读材料还在。
        """
        return self._c.get_value("ques/delete", {"questionId": question_id})

    # ------------------------------------------------------------------
    # §3.1 答题
    # ------------------------------------------------------------------
    def answer(self, rm_id, question):
        """§3.1 答题。

        .. warning::
           **实测打不通。** 文档（``55226315`` §3.1）写的是
           ``POST /api/aigc/ques/ans {rmId, question}``，但：

           * 两个主机（``uaigc.unipus.cn`` / ``aigc.unipus.cn``）都回
             **HTTP 404**``{"status":404,"error":"Not Found",
             "path":"/api/aigc/ques/ans"}``；
           * 换了三个路径名（``ques/ans`` / ``ques/answer`` / ``ques/ansQues``）
             和两种入参（带不带 ``quesCode``）**全是 404**；
           * **前端产物里也搜不到它**——67 个 bundle 里 ``ques/`` 开头的端点
             有 ``ques/gen`` / ``ques/word`` / ``ques/top`` 等一批，**没有
             ``ques/ans``**。跟 §10.1 那三个空壳端点是同一类。

           所以 ``questions answer`` **只保留 CLI 入口**，别在上面建链——
           要"答题"只能人工。见内部的接口记录 §9.8。
        """
        return self._c.post("ques/ans", {"rmId": rm_id, "question": question})

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _soft_list(self, path, payload, empty_msg):
        """把"这是个空态"的 ``code=1001`` 变成空列表，其余错误照抛。"""
        try:
            value = self._c.get_value(path, payload)
        except AigcError as e:
            if empty_msg in str(e):
                return []
            raise
        if isinstance(value, list):
            return value
        return []


# ----------------------------------------------------------------------
# 模块级小工具
# ----------------------------------------------------------------------
def _ploys(ploys):
    """把 ``questionPloyList`` 归一成 ``[{"code": int, "count": int}]``。

    两种写法都收：``[{"code": 1010, "count": 3}]`` 或 ``{1010: 3}``。
    """
    if isinstance(ploys, dict):
        ploys = [{"code": c, "count": n} for c, n in ploys.items()]
    out = []
    for p in ploys:
        if isinstance(p, dict):
            item = dict(p)
            if "code" in item:
                item["code"] = int(item["code"])
            if "count" in item:
                item["count"] = int(item["count"])
            out.append(item)
        else:
            code, count = p
            out.append({"code": int(code), "count": int(count)})
    return out


def questions_of(data):
    """从 op12 的 ``responseData`` 里取出 ``questionList``。

    ``responseData`` 是 ``{"content": "<JSON 字符串>"}``，
    **要 ``loads`` 两次**（``wait_task`` 已经做了第一层）。
    拿不到就抛——**不要静默返回空列表**，那会把"结构变了"伪装成"没出题"。
    """
    if not isinstance(data, dict):
        raise AigcError("出题结果不是预期结构", path="op12", payload=data)
    content = data.get("content")
    if content is None:
        # 有些路径下 responseData 本身就可能是题目结构（平台改过行为）。
        if "questionList" in data:
            return data["questionList"]
        raise AigcError("出题结果里没有 content", path="op12", payload=data)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as e:
            raise AigcError(f"出题结果里的 content 不是 JSON: {e}",
                            path="op12", payload=content[:400])
    if isinstance(content, dict) and "questionList" in content:
        return content["questionList"]
    if isinstance(content, list):
        return content
    raise AigcError("出题结果的 content 结构不认识", path="op12", payload=content)


def is_terminal(status):
    """这条链路里 ``status`` 是不是终态（给调用方自建轮询时用）。"""
    return status in TaskStatus.TERMINAL
