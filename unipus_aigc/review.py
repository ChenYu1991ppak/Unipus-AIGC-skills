# -*- coding: utf-8 -*-
"""智能评阅（作文）。

调用链（详见 docs/call-chains.md）::

    wm/create {title,type:"1",subType:93,topic,content,level}   -> wmId
    task/submit {operation:35, submitData:{topic,content,level,
                 wmId,subType:93,evaluationName,fileUrl}}       -> taskId
    task/queryTask {taskId} 轮询到 status=3，responseData 里是完整评阅结果

注意：不要用 wm/detail 取结果，它的 ``evaluation`` 字段不会填充。
"""

import json
import time

from .constants import Level, Operation, SubType


class ReviewAPI:
    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def essay(self, content, topic="", level=Level.COLLEGE, title=None,
              *, poll_interval=4, timeout=300):
        """评阅一篇作文，返回结构化的评阅结果。

        :param content: 作文正文
        :param topic: 题目；留空用 ``title`` 兜底
        :param level: 学段，见 :class:`~unipus_aigc.constants.Level`
        :return: 评阅结果 dict，主要字段见 :meth:`format_report`

        结果结构（``task/queryTask`` 的 responseData）::

            {
              "score": 2552,               # 加权总分 = 各分项之和
              "totalGrade": 3.0,
              "contentScore": 1520, "languageScore": 700,
              "organizationScore": 150, "mechanicsScore": 190,
              "comment": "总评……",        # content/language/... 是评语字符串
              "feature": {"tokens": 18, "sentCount": 2, ...},      # 语言特征统计
              "correct": [
                 {"index": "1.1", "content": "原句", "suggest_sent": "建议句",
                  "errorList": [{"typeName": "名词的数错误", "typeId": "WS-N1",
                                 "desc": "解析……", "suggest_sent": "改写句",
                                 "words": [{"i": 5, "j": 12}]}]}
              ]
            }
        """
        title = title or topic or f"作文评阅-{time.strftime('%Y%m%d-%H%M%S')}"
        topic = topic or title

        rec = self._c.get_value("wm/create", {
            "title": title,
            "type": "1",
            "subType": SubType.CompositionReview,
            "topic": topic,
            "content": content,
            "level": int(level),
        })
        wm_id = (rec or {}).get("wmId")
        if not wm_id:
            raise ValueError(f"wm/create 未返回 wmId: {rec}")

        task_id = self._c.submit_task(Operation.CompositionReview, {
            "topic": topic,
            "content": content,
            "level": int(level),
            "wmId": wm_id,
            "subType": SubType.CompositionReview,
            "evaluationName": title,
            "fileUrl": "",
        })
        result = self._c.wait_task(task_id, interval=poll_interval, timeout=timeout)
        if isinstance(result, dict):
            result.setdefault("wmId", wm_id)
            result.setdefault("taskId", task_id)
        return result

    # ------------------------------------------------------------------
    # 记录管理
    # ------------------------------------------------------------------
    def records(self, page=1, size=10, type_="1"):
        """历史记录。后端强制要求 ``type``，不传会报 "type不能为空!"。"""
        return self._c.get_value("wm/list", {"type": type_, "pageNum": page,
                                             "pageSize": size})

    def detail(self, wm_id):
        """记录详情。``evaluation`` 字段不会填充，评阅结果请用 taskId 查。"""
        return self._c.get_value("wm/detail", {"wmId": str(wm_id)})

    def delete(self, *wm_ids):
        out = [self._c.get_value("wm/delete", {"wmId": str(w)}) for w in wm_ids]
        return out if len(out) > 1 else out[0]

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def format_report(result):
        """把评阅结果渲染成可读文本，方便 CLI / 日志输出。"""
        if not isinstance(result, dict):
            return str(result)

        lines = []
        score = result.get("score")
        if score is None:
            score = result.get("totalScore")
        if score is not None:
            # score / totalScore 是加权总分；分项是加权后的得分（不是百分制）
            lines.append(f"总分: {score}")
            subs = [("内容", result.get("contentScore")),
                    ("语言", result.get("languageScore")),
                    ("结构", result.get("organizationScore")),
                    ("规范", result.get("mechanicsScore"))]
            sub = "  ".join(f"{n} {v}" for n, v in subs if v is not None)
            if sub:
                lines.append(f"分项: {sub}")
            feats = result.get("feature") or {}
            if feats.get("tokens") is not None:
                lines.append(
                    f"字数统计: 词数 {feats.get('tokens')}  句数 {feats.get('sentCount')} "
                    f"段落 {feats.get('paraCount')}  平均句长 {feats.get('avgSentLen')}")
        if result.get("comment"):
            lines.append(f"总评: {result['comment']}")

        # 逐句纠错：correct[] -> errorList[]（typeName / desc / suggest_sent）
        items = result.get("correct") or result.get("evaluationList") or []
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            lines.append(f"\n[{item.get('index') or i}] {item.get('content', '')}")
            for err in item.get("errorList", []) or []:
                lines.append(
                    f"    - 类型: {err.get('typeName', '')} ({err.get('typeId', '')})"
                    f"\n      说明: {err.get('desc', '')}"
                    f"\n      建议: {err.get('suggest_sent', '')}"
                )
            if not item.get("errorList") and item.get("suggest_sent"):
                lines.append(f"    - 建议: {item['suggest_sent']}")
        return "\n".join(lines)

    @staticmethod
    def dump(result):
        """评阅结果的 JSON 文本。"""
        return json.dumps(result, ensure_ascii=False, indent=2)
