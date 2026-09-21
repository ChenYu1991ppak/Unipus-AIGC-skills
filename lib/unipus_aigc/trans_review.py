# -*- coding: utf-8 -*-
"""翻译评阅（operation 36）。

调用链（详见 docs/call-chains.md §8）::

    (1) wm/create {title,type:"2",langFrom,langTo,content,translation} -> wmId
          ⚠️ type 是**字符串** "2"（跟口语评阅的 "3" 同理）。
          ⚠️ 只有 {title,type} 也能建出记录，其余字段是"记录里存着好看"。

    (2) task/submit {operation:36,
                     submitData:{wmId,srcLang,srcText,tgtLang,tgtText,subType:70}}
                                                                    -> taskId

    (3) task/queryTask {taskId} 轮询到 status=3
          └─> value.responseData 是 JSON **字符串**，再 loads 一次，
              形状是 {"uuid","code","message","score","waiting_jobs_num","time_cost"}

.. warning::
   **这个 operation 叫「翻译评阅」，不是「译后编辑」。**

   本仓库早期把它记成"译后编辑"，那是个**错误的名字**，已经改掉。依据三条：

   * 前端组件标题写死 ``children:"翻译评阅"``，界面上只渲染 ``评阅得分：{score}``；
   * 接口文档的 operation 枚举表：``36 //翻译评阅``；subType 表：``70-翻译评阅``；
   * **实测它只回一个分数**（见下）。

   所以：**没有"改后的译文"这回事**。``queryTask`` 的 ``responseData`` 里
   只有 ``score``，一个字的译文都没有。谁想要润色后的译文，这个接口给不了——
   拿到的分数是"这段译文翻得怎么样"，不是"应该怎么翻"。

.. warning::
   实测（2026-09-20）跟逆向推测有三处**实质性出入**，这里以实测为准：

   **1. 只回一个分数，没有译文。** ``queryTask`` 的真实形状::

       {"evaluation": ...}  # 上面那层 value 里
       responseData = "{\\"uuid\\":\\"…\\",\\"code\\":\\"200\\",\\"message\\":\\"Success.\\",
                        \\"score\\":73.91,\\"waiting_jobs_num\\":0,\\"time_cost\\":0.3716}"

   ``score`` 是百分制浮点。同一份输入**可复现**（连跑两次同一个 97.66）。

   **2. ``wm/detail`` 对翻译评阅是**可用**的，而且 ``evaluation`` 就在**顶层**。**
   三种评阅在这一层上各不相同，别互相抄::

       作文评阅（type=1）  顶层 evaluation 恒为 null  <- 不能用 wm/detail 取结果
       翻译评阅（type=2）  顶层 evaluation **填好**   <- 本文档
       口语评阅（type=3）  顶层 null，内容在 evaluationList[0].evaluation

   ``wm/detail`` 里的 ``translation`` **是把提交的 ``tgtText`` 原样存下来的**，
   不是平台改过的——别把它当"译后编辑的结果"读。同时它还回
   ``isEvaluation: true`` 和 ``langFrom`` / ``langTo``。

   **3. 语种码写错不报错，只静默返回假分数。** 这是这个接口最坑的地方，
   详见 :func:`TransReviewAPI.submit` 的 warning。

.. warning::
   **异步但很快。** ``task/submit`` 回 ``status=1``、``responseData: null``
   （注意：不是口语评阅那种 ``status=2``），随后 ``queryTask`` 出 ``status=3``。
   实测 ``time_cost`` 在 0.3–0.5 秒量级，一次 ``wait_task`` 就够。
"""

import json
import time

from .constants import Operation, SubType
from .errors import StillRunning, TaskTimeout

#: ``wm/create`` / ``wm/list`` 用 ``type`` 区分记录类型，**字符串**。
#: ``1`` 作文评阅 / ``2`` 翻译评阅 / ``3`` 口语评阅。
#: （``wm/list`` 和 ``wm/detail`` 的**响应**里回的都是整数 ``2``。）
RECORD_TYPE = "2"

#: 实测**不吃**的字段，但文档和前端都写着它。给上，免得平台哪天开始校验。
#: 实测对照：``subType:70`` 与 ``subType:93`` 在同一份输入上给出一模一样的
#: 97.66，说明它当前**不参与**计算——但**照文档给 70**，不要"优化"掉。
DEFAULT_SUB_TYPE = SubType.TranslationReview

#: 平台口头支持的语种（文档原文："目前仅支持中文英文。'en' 为英文，'zh' 为中文"）。
#: 实测两字母小写是**唯一**走对路子的写法，三字母／大写会静默给假分数。
SUPPORTED_LANGS = ("en", "zh")


class TransReviewAPI:
    """翻译评阅的业务封装。挂在 ``client.trans_review`` 上。

    .. note::
       **它不产出译文。** 想要译文请用 :class:`~unipus_aigc.translate.TranslateAPI`
       （文本 ``translate/text``、文档 ``translate/submit-doc``）。这里只给分数。
    """

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def submit(self, src_text, tgt_text, *, src_lang, tgt_lang, title=None,
               wm_id=None, sub_type=None):
        """**只提交，不等结果**：``wm/create`` + ``task/submit``。

        :param src_text: 题目原文（写进记录的 ``content``）
        :param tgt_text: 待评的译文（写进 ``submitData.tgtText``，
            也写进记录的 ``translation``）
        :param src_lang: 原文语种，**必须是小写两字母** ``en`` / ``zh``
        :param tgt_lang: 译文语种，同上，且**必须等于记录的 ``langTo``**
        :param wm_id: 复用已有记录。给了它就会先读一次 ``wm/detail``，
            拿记录自己的 ``langFrom``/``langTo`` 当准——**传进来的
            ``src_lang``/``tgt_lang`` 若跟记录对不上会直接报错**（见下）
        :return: ``{"wmId", "taskId", "srcLang", "tgtLang", "title"}``

        .. warning::
           **语种码写错，平台不报错，只给一个假分数。** 实测矩阵
           （记录 ``en -> zh``，其余全对，只动语种码）::

               srcLang/tgtLang   分数      判读
               en / zh           97.66     正常
               en / zho          42.58     走了另一条错路（不是拒绝）
               eng / zho         42.58     同上
               EN / ZH           42.58     同上
               zh / en            0.00      语言对反了
               en / ja            0.00      不支持的目标语
               en / en            0.00      同语

           全程 ``status=3``、``code:"200"``、``message:"Success."``——
           **一次错都不报**。而且 ``42.58`` 那档并非"固定拒答分"：
           同一个错码下好译文 42.58、劣译文 43.27，分数**是变的**，
           所以它是"算了个别的东西"，比 0.0 更难发现。

           决定性的一条：**语言对由 ``wm`` 记录上的 ``langFrom``/``langTo``
           说了算**。拿一条真 ``zh -> en`` 的记录提交，好译文 95.67、劣译文
           69.29，正常给分；而在 ``en -> zh`` 的记录上硬提交 ``tgtLang:"en"``，
           哪怕英文写得再好也是 0.0。

           所以本方法对复用的 ``wm_id`` 做了硬校验：读回记录的语种，
           跟传进来的对不上就 ``raise ValueError``，**不把一个 0.0 交出去**。
           要评另一对语种，请新建记录。
        """
        src_lang = _check_lang(src_lang, "src_lang")
        tgt_lang = _check_lang(tgt_lang, "tgt_lang")

        if wm_id:
            rec = self.detail(wm_id) or {}
            if rec.get("type") not in (RECORD_TYPE, int(RECORD_TYPE)):
                raise ValueError(
                    f"wmId {wm_id} 不是翻译评阅记录（type={rec.get('type')!r}，"
                    f"应为 {RECORD_TYPE}）——别的类型的记录交上来只会拿到假分数。")
            rec_from, rec_to = rec.get("langFrom"), rec.get("langTo")
            if rec_from and rec_to and (rec_from, rec_to) != (src_lang, tgt_lang):
                raise ValueError(
                    f"记录 {wm_id} 的语言对是 {rec_from} -> {rec_to}，"
                    f"跟传进来的 {src_lang} -> {tgt_lang} 对不上。"
                    f"平台不会为此报错，只会静默返回 0.0 之类的假分数，"
                    f"所以这里直接挡住。要评另一对语种请新建记录（不要传 wm_id）。")
            wm_id = str(wm_id)
        else:
            title = title or f"翻译评阅-{time.strftime('%Y%m%d-%H%M%S')}"
            rec = {"title": title, "type": RECORD_TYPE,
                   "langFrom": src_lang, "langTo": tgt_lang,
                   "content": src_text, "translation": tgt_text}
            got = self._c.get_value("wm/create", rec) or {}
            wm_id = got.get("wmId")
            if not wm_id:
                raise ValueError(f"wm/create 未返回 wmId: {got}")

        payload = {
            "wmId": str(wm_id),
            "srcLang": src_lang,
            "srcText": src_text,
            "tgtLang": tgt_lang,
            "tgtText": tgt_text,
            "subType": int(DEFAULT_SUB_TYPE if sub_type is None else sub_type),
        }
        task_id = self._c.submit_task(Operation.TranslationReview, payload)
        return {"wmId": str(wm_id), "taskId": task_id,
                "srcLang": src_lang, "tgtLang": tgt_lang, "title": title}

    def review(self, src_text, tgt_text, *, src_lang, tgt_lang, title=None,
               wm_id=None, poll_interval=3, timeout=120):
        """评阅一份译文，返回结果字典（提交 + 等待一次做完）。

        结果形状见模块 docstring。要点：**分数在 ``score``**（百分制），
        此外只有 ``uuid`` / ``code`` / ``message`` / ``waiting_jobs_num`` /
        ``time_cost``——**没有任何译文**。

        实测这个 operation 很快（``time_cost`` 0.3–0.5 秒），
        默认 120s 超时足够宽裕。
        """
        sub = self.submit(src_text, tgt_text, src_lang=src_lang,
                          tgt_lang=tgt_lang, title=title, wm_id=wm_id)
        result = self._c.wait_task(sub["taskId"], interval=poll_interval,
                                   timeout=timeout)
        if isinstance(result, dict):
            result.setdefault("wmId", sub["wmId"])
            result.setdefault("taskId", sub["taskId"])
        return result

    # ------------------------------------------------------------------
    # 取结果
    # ------------------------------------------------------------------
    def poll(self, task_id, *, interval=3, timeout=60):
        """**短轮询**评阅结果；没出结果就抛 :class:`StillRunning`。"""
        try:
            result = self._c.wait_task(task_id, interval=interval, timeout=timeout)
        except TaskTimeout as e:
            raise StillRunning(
                f"翻译评阅任务 {task_id} 仍在处理中，{timeout}s 内未出结果。"
                f"稍后用同一个 taskId 再 poll 一次。",
                path="task/queryTask", payload=e.payload,
            ) from e
        if isinstance(result, dict):
            result.setdefault("taskId", task_id)
        return result

    def get(self, task_id):
        """**只查一次**，不轮询。未完成返回 ``None``。

        跟口语评阅不同，这条链路 ``status=2`` 时 ``responseData`` 还是
        ``null``（口语评阅那边填的是一个 ``correctId`` 句柄），
        所以这里不需要额外的"句柄识别"。
        """
        raw = self._c.query_task(task_id)
        result = self._c.parse_task_result(raw)
        if isinstance(result, dict):
            result.setdefault("taskId", task_id)
        return result

    # ------------------------------------------------------------------
    # 记录管理
    # ------------------------------------------------------------------
    def records(self, page=1, size=10, type_=RECORD_TYPE):
        """历史记录。后端强制要求 ``type``，不传会报 "type不能为空!"。

        默认 ``type_="2"`` 只列翻译评阅——**``cli cleanup`` 扫的是
        ``type="1"``（作文评阅），看不见这里的记录**，清理要显式用
        :meth:`delete`。

        注意响应里的行是**整数** ``type: 2``，且 ``langFrom``/``langTo``
        在列表行里**不返回**（``wm/detail`` 才有）。
        """
        return self._c.get_value("wm/list", {"type": type_, "pageNum": page,
                                             "pageSize": size})

    def detail(self, wm_id):
        """记录详情。

        .. note::
           翻译评阅的 ``evaluation`` 就在**顶层**且**已填好**——
           跟作文评阅（顶层恒 null）和口语评阅（埋在 ``evaluationList[0]``）
           都不一样。同时回的还有 ``langFrom`` / ``langTo`` /
           ``content``（题目原文）/ ``translation``（**原样存下的待评译文**）。

           这里的 ``translation`` **不是**平台的产出，就是提交时那份
           ``tgtText``。别读成"改后的译文"。
        """
        return self._c.get_value("wm/detail", {"wmId": str(wm_id)})

    def delete(self, *wm_ids):
        """按 ``wmId`` 删记录。**这是清理翻译评阅记录的唯一入口**——
        ``cli cleanup`` 只扫 ``type="1"``，看不到翻译评阅。"""
        out = [self._c.get_value("wm/delete", {"wmId": str(w)}) for w in wm_ids]
        return out if len(out) > 1 else out[0]

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def score_of(result):
        """取分数（百分制）。没出结果返回 ``None``。"""
        ev = _evaluation_of(result)
        return ev.get("score") if isinstance(ev, dict) else None

    @staticmethod
    def source_of(result):
        """取题目原文（``content``）。取不到返回 ``""``。"""
        if isinstance(result, dict):
            return result.get("content") or ""
        return ""

    @staticmethod
    def translation_of(result):
        """取记录里存的**待评译文**（``translation``）。

        .. warning::
           **这是提交时那份 ``tgtText`` 的原样回吐，不是平台改过的译文。**
           接口没有任何"改后的译文"可给。这个方法存在只是为了读记录，
           别拿它去证明"译后编辑生效了"。
        """
        if isinstance(result, dict):
            return result.get("translation") or ""
        return ""

    @staticmethod
    def format_report(result):
        """把评阅结果渲染成可读文本，方便 CLI / 日志输出。"""
        if not isinstance(result, dict):
            return str(result)

        ev = _evaluation_of(result)
        if not ev:
            return ("(还没有评阅结果)\n"
                    "注：status 未到终态时 responseData 还是 null，"
                    "稍后再 poll 一次。")

        lines = []
        score = ev.get("score")
        lines.append(f"评阅得分: {score}" if score is not None else "评阅得分: (空)")

        if result.get("wmId"):
            lines.append(f"记录 wmId: {result['wmId']}   （删记录用这个，不是 taskId）")
        elif result.get("id"):
            lines.append(f"记录 id  : {result['id']}")

        src = TransReviewAPI.source_of(result)
        if src:
            lines.append(f"题目原文 : {src}")
        tgt = TransReviewAPI.translation_of(result)
        if tgt:
            lines.append(f"待评译文 : {tgt}")

        if ev.get("time_cost") is not None:
            lines.append(f"耗时     : {ev.get('time_cost')} s")
        if ev.get("uuid"):
            lines.append(f"uuid     : {ev['uuid']}")

        lines.append("")
        lines.append("注：本 operation 是**翻译评阅**，只给分数，**不产出改后的译文**。")
        lines.append("    要译文请用 `translate text` / `translate submit-doc`。")
        return "\n".join(lines)

    @staticmethod
    def dump(result):
        """评阅结果的 JSON 文本。"""
        return json.dumps(result, ensure_ascii=False, indent=2)


def _check_lang(code, name):
    """语种码本地校验。

    **平台不校验**——写错了照回 ``status=3`` / ``code:"200"```，只是分数变成
    0.0 或 42.58 那种假数。所以这一道必须在这里做。
    """
    code = str(code or "").strip()
    if code not in SUPPORTED_LANGS:
        raise ValueError(
            f"{name}={code!r} 不是平台认的语种码。文档口径：目前仅支持 "
            f"en（英文）/ zh（中文），**小写两字母**。"
            f"实测三字母（eng/zho）和大写（EN/ZH）都会静默给出假分数"
            f"（不报错），所以这里提前挡住。")
    return code


def _evaluation_of(result):
    """从结果里取出 ``evaluation`` 那个 dict。

    这一档有三种布局，**翻译评阅走的是第一种**：

    1. **``queryTask`` 的 ``responseData`` 本身就是 evaluation**——
       它 ``loads`` 出来就是 ``{"uuid","code","message","score",…}``，
       **外面没有 ``evaluation`` 这层壳**。所以 ``score`` 在顶层。
    2. ``wm/detail`` 的记录行是 ``{"evaluation": {…}, "langFrom": …, …}``。
    3. ``evaluationList[0].evaluation``（口语评阅的布局，这里也认，
       免得哪天平台改了布局把调用方打挂）。

    判据用 ``score``/``uuid``/``time_cost`` 这几个只在 evaluation 里出现的键，
    而不是"有 ``code`` 就算"——外层信封也有 ``code``。
    """
    if not isinstance(result, dict):
        return None
    if any(k in result for k in ("score", "time_cost", "waiting_jobs_num")):
        return result
    ev = result.get("evaluation")
    if isinstance(ev, dict):
        return ev
    rows = result.get("evaluationList")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("evaluation"), dict):
                return row["evaluation"]
    return None
