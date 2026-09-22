# -*- coding: utf-8 -*-
"""同步 operation（11 / 13 / 14 / 15 / 17）：结果就在提交响应里。

平台把 operation 分成两类：

* **异步交互**——``task/submit`` 只给 ``taskId``，结果要 ``task/queryTask`` 轮询。
  §语音合成 的模板（``submit`` → 轮询 → ``responseData``）就是这一类。
* **同步交互**——``task/submit`` 的响应里 ``status`` 已经是 ``3``、
  ``responseData`` 已经填好，**没有轮询这一步**。用异步模板去套会白等到超时。

本模块是后一类的业务封装。底层判据在
:meth:`~unipus_aigc.client.UnipusAIGC.submit_sync`——它就地看响应里的
``status``，**不**照 operation 查表，因为平台的行为可能变。

.. warning::
   这条通道是**照接口文档实现的第一版，实测证据比别的模块薄**。
   逐条实测结论（2026-09-20，op 各提交一次）见 `内部的接口记录` §5.2：

   * **11 课标问答** —— 通，返回答案。
   * **13 智能答题** —— 回 ``status=9``（文档 7 态枚举之外），**没有结果**。
     本模块如实把它当"语义未知"返回，**不粉饰成成功**。
   * **14 提示词优化** —— 通，返回优化后的提示词。
   * **15 中文提示词翻译** —— 通，但 ``content`` 是**空的**，原因没查出来。
   * **17 知识库查看** —— 通，``docInfo`` 是一个 JSON **字符串**，还要再
     ``loads`` 一次，解出来是 ``doc_name`` / ``doc_url`` 的**列表**。
"""

import json

from .constants import Operation


class SyncAPI:
    """同步 operation 的业务封装。挂在 ``client.sync`` 上。"""

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 通用入口
    # ------------------------------------------------------------------
    def submit(self, operation, submit_data, socket_id=None):
        """通用入口。给 operation 一个不在已知表里的值时也走这条。

        :return: :class:`~unipus_aigc.client.SyncOutcome`
        """
        return self._c.submit_sync(operation, submit_data, socket_id=socket_id)

    def wait(self, outcome, *, interval=3, timeout=60, parse=True):
        """把"其实还在跑"的那种 outcome 兜到终态。

        正常情况**用不上**——同步 operation 在提交响应里就给结果了。留在
        这里是因为 :meth:`~unipus_aigc.client.UnipusAIGC.submit_sync` 刻意
        没有写死"同步 operation 一定不轮询"，万一平台改成先排队（``6``）
        再出结果，调用方有路可走，不会卡死。
        """
        if outcome.done:
            return outcome
        if not outcome.task_id:
            return outcome
        result = self._c.wait_task(outcome.task_id, interval=interval,
                                   timeout=timeout, parse=parse)
        outcome.result = result
        outcome.done = True
        return outcome

    # ------------------------------------------------------------------
    # op 11 课标问答
    # ------------------------------------------------------------------
    def course_standard_qa(self, question, course_standard_id=None):
        """op 11 课标问答（同步）。返回 ``content``（答案）+ ``source`` + ``page``。

        .. warning::
           ``course_standard_id`` **文档标为选填，实测是必填**。文档的字段表里
           ``courseStandardId`` 和 ``content`` 都写 ``false``（可省），实际：

           * 只给 ``content`` → ``code=100`` ``courseStandardId字段不能为空``
           * 只给 ``courseStandardId`` → ``code=100`` ``content字段不能为空``

           两个都得传。合法 id 只有一个来源：``POST /api/aigc/cs/queryList``
           （CLI 是 ``sync standards``），实测返回一行，``courseStandardId`` 是
           ``0``——**``0`` 是合法 id，不是"没选"的哨兵值**。

        :param course_standard_id: 课标 id。``None`` 时不带这个字段，服务端会拒。
        """
        data = {"content": question}
        if course_standard_id is not None:
            data["courseStandardId"] = course_standard_id
        return self._c.submit_sync(Operation.CourseStandardQA, data)

    # ------------------------------------------------------------------
    # op 13 智能答题
    # ------------------------------------------------------------------
    def question_answer(self, rm_id, question):
        """op 13 智能答题（同步）。

        .. warning::
           **实测回 ``status=9``，拿不到结果。** 文档把入参写得很清楚
           （``rmId`` 阅读材料 id + ``question`` 题目内容），提交也不报错，
           但响应里的 ``status`` 既不是成功也不是失败。本方法照原样返回，
           让调用方看到真实的 ``status``——不要在这里替平台补一个结论。
        """
        return self._c.submit_sync(Operation.QuestionAnswer,
                                   {"rmId": rm_id, "question": question})

    # ------------------------------------------------------------------
    # op 14 提示词优化
    # ------------------------------------------------------------------
    def prompt_optimize(self, text):
        """op 14 提示词优化（同步）。返回 ``content`` = 优化后的提示词。"""
        return self._c.submit_sync(Operation.PromptOptimize, {"text": text})

    # ------------------------------------------------------------------
    # op 15 中文提示词翻译
    # ------------------------------------------------------------------
    def prompt_translate(self, text):
        """op 15 中文提示词翻译（同步）。返回 ``content`` = 英文提示词。

        .. warning::
           **实测返回的 ``content`` 是空的**，原因没查出来（提交不报错、
           ``status`` 也没问题）。这里不做兜底、不猜结论——拿到空串就如实
           报空串。
        """
        return self._c.submit_sync(Operation.PromptTranslate, {"text": text})

    # ------------------------------------------------------------------
    # op 17 知识库查看
    # ------------------------------------------------------------------
    def kb_view(self, user_id=None):
        """op 17 知识库查看（同步）。

        返回里的 ``docInfo`` 是**一个 JSON 字符串**，解出来是 ``DocInfo``
        的**列表**——不是单个 dict，字段名是 **snake_case** 的 ``doc_name`` /
        ``doc_url``，``doc_url`` 指向飞书（``feishu.cn``）链接。

        文档口径（字段表）：``operation 17`` 的 ``docInfo`` 是
        *"知识库文档列表 json字符串（List<DocInfo>）"*。本方法把里面那层
        ``json.loads`` 也做了，``outcome.result["docInfo"]`` 直接是列表。
        """
        data = {} if user_id is None else {"userId": user_id}
        outcome = self._c.submit_sync(Operation.KBView, data)
        _expand_doc_info(outcome)
        return outcome

    # ------------------------------------------------------------------
    # 取结果的小工具（三种情形都要认）
    # ------------------------------------------------------------------
    @staticmethod
    def content_of(outcome):
        """取出 op 11/14/15 那三种"结果就是一个字符串"的 ``content``。

        取不到返回 ``None``——**不要在这里把 ``None`` 变成空串**，调用方要能
        区分"没出结果"和"出了个空结果"（op15 实测就是后者）。
        """
        if outcome.done and isinstance(outcome.result, dict):
            return outcome.result.get("content")
        return None

    @staticmethod
    def docs_of(outcome):
        """取出 op17 解析后的文档列表。取不到返回 ``[]``。"""
        if outcome.done and isinstance(outcome.result, dict):
            docs = outcome.result.get("docInfo")
            if isinstance(docs, list):
                return docs
        return []


def _expand_doc_info(outcome):
    """把 op17 结果里的 ``docInfo`` 从 JSON 字符串展开成列表（就地）。"""
    if not (outcome.done and isinstance(outcome.result, dict)):
        return
    raw = outcome.result.get("docInfo")
    if not isinstance(raw, str):
        return
    try:
        outcome.result["docInfo"] = json.loads(raw)
    except ValueError:
        # 解不开就原样留着——上层拿到的还是那个字符串，不会静默变成 None。
        return
