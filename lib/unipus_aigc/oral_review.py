# -*- coding: utf-8 -*-
"""口语评阅（operation 90）。

调用链（详见内部的接口记录 §7）::

    （本地音频先传七牛：common/uploadToken + up-z1.qiniup.com，folder 用 audio）

    (1) wm/create {title,type:"3",content,...}                 -> wmId
          ⚠️ type 是**字符串** "3"，且**不传 subType**——跟作文评阅（type:"1"
          + subType:93）不一样。文档的字段表把口语评阅的 subType 写作 69，
          实测 wm/create **不需要**它；69 只在 grade/list 那边成立。

    (2) task/submit {operation:90,
                     submitData:{wmId,evaluationContent,audioFileUrl}} -> taskId

    (3) task/queryTask {taskId} 轮询到 status=3
          └─> value.responseData 是 JSON **字符串**，再 loads 一次

**口语评阅是异步的**：``task/submit`` 只回 ``taskId``（``status=2``），
结果要 ``queryTask`` 轮出来。

.. warning::
   实测（2026-09-20）跟逆向推测有两处**实质性出入**，这里以实测为准：

    **1. 结果里没有 ``errorSegments`` / ``pronunciationIssues``。**
    逆向前端代码看到的是**展示层映射**（``el(t?.pronunciation)`` 之类），
    不是传输层字段。``queryTask`` 真实返回的形状只有::

        {"evaluation": {"duration": 3744,
                        "feedback": "1. **I**: 发音较准确……整体建议：……",
                        "overall": 98.0,
                        "words": [{"word": "I WENT TO SCHOOL…"}]},
         "evaluationIconUrl": "...", "evaluationName": "学生1",
         "id": "<记录 id>", "wmId": "<wmId>"}

    **逐词/逐句的发音建议在 ``feedback`` 那段文本里**（按 ``I`` / ``WENT`` /
    ``TO`` … 逐词列，结尾一段"整体建议"），不是结构化数组。所以
    :meth:`OralReviewAPI.format_report` 直接把 ``feedback`` 原样打出来，
    **不编造**一个 ``errorSegments`` 字段。

    ``evaluation`` 里还带着一整套**恒为 null** 的字段（``fluency`` /
    ``pronunciation`` / ``integrity`` / ``relevance`` / ``grammar`` /
    ``sentences`` / ``coherence`` / ``asrRes`` / ``keywordPercent`` …）——
    平台把字段骨架整个返回，但这段音频只有 ``duration`` / ``overall`` /
    ``feedback`` / ``words`` 被填上。**别把它们当"评了 0 分"**。

    **2. ``wm/detail`` 对口语评阅是可用的**（跟作文评阅相反）。
    ``wm/detail`` 的**顶层** ``evaluation`` 同样是 ``null``，但
    ``evaluationList[0].evaluation`` 是**填好的**，字段跟 ``queryTask``
    一致，另外还多一个 ``evaluationStatus``（见
    :meth:`OralReviewAPI.detail` 的说明）。

    **还有一处「提交成功但没结果」要认**：``status=2`` 时 ``responseData``
    已经被填成一个 ``{"code":0,"data":{"correctId":"…"}}`` 的**句柄**，
    那不是评阅结果。:meth:`OralReviewAPI.get` 认得这一档，返回 ``None``。

    **3. ``quesType`` 也是硬必填，而且它是在 ``task/submit`` 那一步被拒的。**
    第一次端到端实测（不带 ``quesType``）回的是::

        code=100  quesType字段不能为空  | path=task/submit

    报错路径是 ``task/submit``，**但字段并不在 ``submitData`` 里**——平台是
    顺着 ``wmId`` 回去读那条记录上的 ``quesType``。已用两条记录对照确认：
    ``wm/detail`` 里失败那条是 ``quesType: null``、成功那条是 ``quesType: 1``。
    所以补默认值要补在 **``wm/create``** 上（``DEFAULT_QUES_TYPE``），
    补在 ``submitData`` 里没用。

    **4. 报错只到 ``task/submit``，记录已经建出去了。** ``wm/create`` 在
    ``submit_task`` **之前**跑，所以上面那次 ``code=100`` 的失败仍然在平台上
    留下了一条空记录（``TMP-probe-oral-cli-e2e``）。跟 §4 语音合成、
    §5 的 AI 绘画是同一类毛病：**报错看得见，垃圾数据也留下了**。
"""

import json
import time

from .constants import Operation
from .errors import StillRunning, TaskTimeout

#: ``wm/create`` 和 ``wm/list`` 都用 ``type`` 区分记录类型。
#: ``1`` 作文评阅 / ``2`` 翻译评阅 / ``3`` 口语评阅。**是字符串，别传数字。**
#: （对比：``wm/list`` 的**响应**里 ``type`` 回的是**整数** ``3``。）
RECORD_TYPE = "3"

#: ``quesType`` 实测是硬必填，缺了在 ``task/submit`` 一步被拒
#: （``code=100 quesType字段不能为空``）。文档没标必填，实测必须给。
#:
#: 平台**不读 ``submitData`` 里的 ``quesType``**，而是顺着 ``wmId`` 回去读
#: ``wm/create`` 时写在那条记录上的值——报错路径在 ``task/submit``，字段却要
#: 补在 ``wm/create`` 上。两条对照记录：``quesType: null`` 的那条提交失败、
#: ``quesType: 1`` 的那条成功。
#:
#: 默认给 ``1``（朗读短文，CLI ``--ques-type`` 的文档口径）。已知取值只有这一个，
#: 别的题型没实测过——要用别的值请显式传 ``ques_type``。
DEFAULT_QUES_TYPE = 1


class OralReviewAPI:
    """口语评阅的业务封装。挂在 ``client.oral`` 上。"""

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def submit(self, audio, evaluation_content=None, *, title=None,
               content="", language_type=None, ques_type=None, grade_id=None,
               wm_id=None):
        """**只提交，不等结果**：可选地上传音频 + ``wm/create`` + ``task/submit``。

        :param audio: 本地音频路径，或已经是七牛上的 URL（``http`` 开头则不重传）
        :param evaluation_content: 评测内容（**实测必填**，服务端报
            ``evaluationContent字段不能为空``）。留空则退用 ``content``（朗读原文）
        :param content: 朗读原文。会写进 ``wm/create`` 的 ``content``，
            也就是结果里的"朗读文本"
        :param ques_type: 题目类型，**实测必填**（服务端报
            ``quesType字段不能为空``）。不给则用 :data:`DEFAULT_QUES_TYPE`。
            **这一项要写在 ``wm/create`` 上，放 ``submitData`` 里没用**
        :param wm_id: 复用已有记录（重评同一段音频时用），不给就新建一条
        :return: ``{"wmId", "taskId", "audioFileUrl", "title"}``

        .. warning::
           ``evaluation_content`` **不能省**。文档没把它标成必填，实测是硬必填——
           ``{}`` 和 ``""`` 都回 ``code=100 evaluationContent字段不能为空``。
           ``ques_type`` 同理（见上）。

           还有一层：``wm/create`` 跑在 ``task/submit`` **前面**，所以上面这两个
           校验哪怕失败了，记录也已经建出去了——**报错的同时留下一条空记录**。
        """
        audio_url = audio if str(audio).startswith("http") \
            else self._c.upload_path(audio, folder="audio")

        title = title or f"口语评阅-{time.strftime('%Y%m%d-%H%M%S')}"

        if not wm_id:
            rec = {"title": title, "type": RECORD_TYPE, "content": content}
            # quesType **实测必填**，而且必须写在 wm/create 上（见
            # DEFAULT_QUES_TYPE 的说明）——不给默认值的话，第一次提交会在
            # task/submit 那一步被拒，然后留下一条建好的空记录。
            rec["quesType"] = int(ques_type if ques_type is not None
                                  else DEFAULT_QUES_TYPE)
            # 这两个都是**可选**的：实测只给 title+type 就能建记录。
            # languageType 是口语评阅自己的字段，gradeId 是学段 id（来自 grade/list）。
            if language_type is not None:
                rec["languageType"] = int(language_type)
            if grade_id is not None:
                rec["gradeId"] = str(grade_id)
            got = self._c.get_value("wm/create", rec) or {}
            wm_id = got.get("wmId")
            if not wm_id:
                raise ValueError(f"wm/create 未返回 wmId: {got}")

        payload = {
            "wmId": str(wm_id),
            "evaluationContent": evaluation_content or content,
            "audioFileUrl": audio_url,
        }
        task_id = self._c.submit_task(Operation.OralReview, payload)
        return {"wmId": str(wm_id), "taskId": task_id,
                "audioFileUrl": audio_url, "title": title}

    def review(self, audio, evaluation_content=None, *, title=None, content="",
               language_type=None, ques_type=None, grade_id=None,
               poll_interval=4, timeout=300):
        """评阅一段音频，返回结构化的评阅结果（提交 + 等待一次做完）。

        结果结构见模块 docstring。要点：分数在 ``evaluation.overall``（百分制），
        时长在 ``evaluation.duration``（**毫秒**），逐词/逐句的发音建议在
        ``evaluation.feedback`` 那段文本里。
        """
        sub = self.submit(audio, evaluation_content, title=title, content=content,
                          language_type=language_type, ques_type=ques_type,
                          grade_id=grade_id)
        result = self._c.wait_task(sub["taskId"], interval=poll_interval,
                                   timeout=timeout)
        if isinstance(result, dict):
            result.setdefault("wmId", sub["wmId"])
            result.setdefault("taskId", sub["taskId"])
        return result

    # ------------------------------------------------------------------
    # 取结果
    # ------------------------------------------------------------------
    def poll(self, task_id, *, interval=4, timeout=60):
        """**短轮询**评阅结果；没出结果就抛 :class:`StillRunning`。"""
        try:
            result = self._c.wait_task(task_id, interval=interval, timeout=timeout)
        except TaskTimeout as e:
            raise StillRunning(
                f"口语评阅任务 {task_id} 仍在处理中，{timeout}s 内未出结果。"
                f"稍后用同一个 taskId 再 poll 一次。",
                path="task/queryTask", payload=e.payload,
            ) from e
        if isinstance(result, dict):
            result.setdefault("taskId", task_id)
        return result

    def get(self, task_id):
        """**只查一次**，不轮询。未完成返回 ``None``。

        .. warning::
           ``status=2``（执行中）时 ``responseData`` **不是空的**——平台已经
           填了一个 ``{"code":0,"data":{"correctId":"…"}}`` 的**句柄**进去。
           那个 dict 里没有 ``evaluation``，不是评阅结果。本方法认得这一档：
           没有 ``evaluation`` 就一律当"还没出结果"，返回 ``None``——
           **不把一个句柄当结果交出去**。
        """
        raw = self._c.query_task(task_id)
        result = self._c.parse_task_result(raw)
        if isinstance(result, dict):
            if not isinstance(result.get("evaluation"), dict):
                return None
            result.setdefault("taskId", task_id)
        return result

    # ------------------------------------------------------------------
    # 记录管理
    # ------------------------------------------------------------------
    def records(self, page=1, size=10, type_=RECORD_TYPE):
        """历史记录。后端强制要求 ``type``，不传会报 "type不能为空!"。

        默认 ``type_="3"`` 只列口语评阅——**作文评阅默认是 ``type_="1"``，
        ``cli cleanup`` 走的是那一条，所以它看不见口语评阅的记录**
        （清理要显式用 :meth:`delete`）。
        """
        return self._c.get_value("wm/list", {"type": type_, "pageNum": page,
                                             "pageSize": size})

    def detail(self, wm_id):
        """记录详情。

        .. note::
           跟作文评阅**不一样**：口语评阅的详情里 ``evaluationList[0].evaluation``
           是**填好的**（顶层 ``evaluation`` 仍是 ``null``，别读错层）。
           每行还带一个 ``evaluationStatus``，取值 ``1/2`` 是评阅中、
           ``3`` 是成功、``4``／``8``／``9`` 不出结果——这是**记录行的 4 态编号，
           跟 ``queryTask`` 的 7 态不是同一套**，别对着读。
        """
        return self._c.get_value("wm/detail", {"wmId": str(wm_id)})

    def delete(self, *wm_ids):
        """按 ``wmId`` 删记录。**这是清理口语评阅记录的唯一入口**——
        ``cli cleanup`` 只扫 ``type="1"``，看不到口语评阅。"""
        out = [self._c.get_value("wm/delete", {"wmId": str(w)}) for w in wm_ids]
        return out if len(out) > 1 else out[0]

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def reading_text_of(result):
        """取朗读原文（结果里的 ``content``）。取不到返回 ``""``。"""
        if isinstance(result, dict):
            return result.get("content") or ""
        return ""

    @staticmethod
    def score_of(result):
        """取总分（``evaluation.overall``，百分制）。没出结果返回 ``None``。"""
        ev = _evaluation_of(result)
        return ev.get("overall") if ev else None

    @staticmethod
    def feedback_of(result):
        """取发音反馈原文（``evaluation.feedback``）。

        **这就是逐词/逐句建议的所在**——按词列，结尾一段"整体建议"。
        返回 ``str``；没出结果返回 ``""``。
        """
        ev = _evaluation_of(result)
        text = ev.get("feedback") if ev else None
        if isinstance(text, str):
            return text
        if text is None:
            return ""
        # feedback 在别的 operation 下是 JSON 字符串，这里没实测到；原样兜底。
        return json.dumps(text, ensure_ascii=False)

    @staticmethod
    def format_report(result):
        """把评阅结果渲染成可读文本，方便 CLI / 日志输出。"""
        if not isinstance(result, dict):
            return str(result)

        ev = _evaluation_of(result)
        if not ev:
            return ("(还没有评阅结果)\n"
                    "注：status 未到终态时 responseData 里可能只有一个 "
                    "correctId 句柄，那不是结果。")

        lines = []
        name = result.get("evaluationName")
        overall = ev.get("overall")
        lines.append(f"总分  : {overall}" if overall is not None else "总分  : (空)")
        dur = ev.get("duration")
        if dur is not None:
            # duration 实测是**毫秒**（3744 对应一句 10 词的话）
            lines.append(f"音频时长: {dur} ms")
        if name:
            lines.append(f"评阅人: {name}")
        if result.get("id"):
            lines.append(f"记录 id: {result['id']}   （删记录用这个，不是 taskId）")

        text = OralReviewAPI.reading_text_of(result)
        if text:
            lines.append(f"朗读文本: {text}")

        words = ev.get("words") or []
        joined = " ".join(w.get("word", "") for w in words if isinstance(w, dict))
        if joined:
            lines.append(f"识别结果: {joined}")

        fb = OralReviewAPI.feedback_of(result)
        if fb:
            lines.append("")
            lines.append("--- 发音反馈 ---")
            lines.append(fb)

        # 平台把一整套字段骨架都返回了，只有少数几个被填上。这里点一句，
        # 免得下游把 null 当"评了 0 分"。
        nulls = [k for k in ("fluency", "pronunciation", "integrity",
                             "relevance", "grammar", "sentences", "coherence")
                 if k in ev and ev.get(k) is None]
        if nulls:
            lines.append("")
            lines.append(f"（{'/'.join(nulls)} 为 null——平台返回了字段骨架但没填，"
                         f"不是 0 分）")
        return "\n".join(lines)

    @staticmethod
    def dump(result):
        """评阅结果的 JSON 文本。"""
        return json.dumps(result, ensure_ascii=False, indent=2)


def _evaluation_of(result):
    """从结果里取出 ``evaluation``。

    ``queryTask`` 的结果和 ``wm/detail`` 的记录行**层级不同**：
    前者 ``evaluation`` 在顶层，后者埋在 ``evaluationList[0]`` 里。
    两条都认，省得调用方自己判。
    """
    if not isinstance(result, dict):
        return None
    ev = result.get("evaluation")
    if isinstance(ev, dict):
        return ev
    rows = result.get("evaluationList")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("evaluation"), dict):
                return row["evaluation"]
    return None
