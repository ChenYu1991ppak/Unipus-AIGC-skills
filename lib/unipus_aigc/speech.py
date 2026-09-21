# -*- coding: utf-8 -*-
"""语音合成（operation 9）。

调用链（详见 docs/call-chains.md）::

    合成:  task/submit(operation=9, submitData={...}) -> task/queryTask 轮询
    记录:  speech/urlList 查历史 / speech/delUrl 删记录

提交字段（接口文档口径，全部实测跑通）::

    text       string  必传   要合成的文本
    speaker    string  必传   音色，取值见 SPEAKERS
    type       int     必传   1 单人 / 2 多人
    language   int     必传   1 中文 / 2 英文
    speed      double  必传   语速，1.0 为原速
    volume     double  选传   音量，文档给的区间是 0.9 ~ 1.2
    audioType  string  必传   音频格式（实测 ``mp3``）
    subType    int     必传   4 语音合成 / 38 生成听力音频

**这是全平台最标准的一条异步链路**——跟 12/16/18/33/34/35 等 operation
走的是同一套 ``task/submit`` + ``queryTask``，只是 ``submitData`` 的字段不同。
把它当作其它 operation 的模板。

音色表在文档里被压成了一列（``中文小明zh_ming`` 这样连在一起），名字和参数
对不上号，所以 :data:`SPEAKERS` 是**实测**出来的，不是抄文档。
"""

import time

from .errors import StillRunning, TaskFailed, TaskTimeout


class Speaker:
    """一个音色。``name`` 是可读名，``param`` 是提交时 ``speaker`` 字段的值。"""

    def __init__(self, param, name, lang):
        self.param = param
        self.name = name
        self.lang = lang

    def __repr__(self):
        return f"Speaker({self.param!r}, {self.name!r}, {self.lang!r})"


class SpeechAPI:
    """语音合成。"""

    #: 实测可用的音色**白名单**。
    #:
    #: 只收下真的跑通、拿到 ``audioUrl`` 的那些。文档的音色表被压成一列、
    #: 名参对应关系不可读，所以这里以实测为准。
    #:
    #: 两种**典型失败**（都实测过，见 SKILL.md）:
    #:
    #: * 拼错的名字（``zh_xiaoming`` / ``en_lukas`` / ``us_anna``）——接口回
    #:   HTTP 500 ``server error!``，**但记录行照样建出来**，只是 ``audioUrl``
    #:   永远是 null。看到 500 不要以为"什么都没发生"。
    #: * ``us_trump`` —— 提交成功、任务卡在 status=2 到不了终态，实测 60 秒
    #:   仍未完成。不是干净的失败，所以不放进白名单。
    SPEAKERS = (
        Speaker("zh_ming", "中文男声（小明）", 1),
        Speaker("zh_xiaoxiao", "中文小晓", 1),
        Speaker("zh_youyou", "中文悠悠", 1),
        Speaker("en_luka", "英式发音（Luka）", 2),
        Speaker("us_annie", "美式发音（Annie）", 2),
    )

    SPEAKER_PARAMS = tuple(s.param for s in SPEAKERS)

    #: ``subType``：4 语音合成 / 38 生成听力音频。
    SUBTYPE_SYNTHESIS = 4
    SUBTYPE_LISTENING = 38

    def __init__(self, client):
        self._c = client

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------
    def submit(self, text, speaker="zh_youyou", *, type_=1, language=None,
               speed=1.0, volume=1.0, audio_type="mp3",
               sub_type=SUBTYPE_SYNTHESIS):
        """只提交，秒级返回 ``taskId``。

        :param language: ``1`` 中文 / ``2`` 英文。不给就按音色前缀猜
            （``zh_*`` -> 1，其余 -> 2）——实测这个字段跟音色不匹配时
            任务会失败或卡住，所以猜出来的值也会一并返回，便于调用方核对。
        :return: ``{"taskId": ..., "speaker": ..., "language": ...}``
        """
        self.check_speaker(speaker)
        if language is None:
            language = 1 if speaker.startswith("zh") else 2
        body = {
            "text": text,
            "speaker": speaker,
            "type": type_,
            "language": language,
            "speed": speed,
            "volume": volume,
            "audioType": audio_type,
            "subType": sub_type,
        }
        task_id = self._c.submit_task(9, body)
        return {"taskId": task_id, "speaker": speaker, "language": language,
                "subType": sub_type}

    @classmethod
    def check_speaker(cls, speaker):
        """音色不在白名单里就拒绝——不要白白在平台上留一条废记录。

        平台对拼错的音色回 HTTP 500，但**仍然建记录**，所以"让它自己去报错"
        是个坏策略：错误看得见，垃圾数据留下了。
        """
        if speaker not in cls.SPEAKER_PARAMS:
            raise ValueError(
                f"未知音色 {speaker!r}。可用：{', '.join(cls.SPEAKER_PARAMS)}"
                f"（跑 `speech speakers` 看中文说明）")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, task_id):
        """查一次。未完成返回 ``None``，失败抛 :class:`TaskFailed`。"""
        return self._c.parse_task_result(self._c.query_task(task_id))

    def wait(self, task_id, *, poll_interval=2, timeout=120):
        """轮询到出结果。合成通常几秒，默认 120s 足够。"""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._c.query_task(task_id)
            result = self._c.parse_task_result(last)
            if result is not None:
                # 任务成功但没给音频地址——平台偶尔会这样，按失败处理，
                # 否则调用方拿到一个 audioUrl 为 null 的"成功"结果。
                if isinstance(result, dict) and not result.get("audioUrl"):
                    raise TaskFailed(
                        f"合成任务 {task_id} 状态成功但没有 audioUrl",
                        path="task/queryTask", payload=result)
                return result
            time.sleep(poll_interval)
        raise TaskTimeout(f"合成任务 {task_id} 在 {timeout}s 内未完成",
                          path="task/queryTask", payload=last)

    def poll(self, task_id, *, poll_interval=2, timeout=60):
        """短轮询：没出结果就抛 :class:`StillRunning`（CLI 映射成退出码 3）。"""
        try:
            return self.wait(task_id, poll_interval=poll_interval, timeout=timeout)
        except TaskTimeout as e:
            raise StillRunning(
                f"合成任务 {task_id} 仍在处理中，{timeout}s 内未出结果。"
                f"稍后用同一个 taskId 再 poll 一次。",
                path="task/queryTask", payload=e.payload,
            ) from e

    def say(self, text, speaker="zh_youyou", *, timeout=120, **kw):
        """提交 + 等待，一次拿到结果。合成是秒级的，不用像文档翻译那样拆两步。"""
        sub = self.submit(text, speaker, **kw)
        result = self.wait(sub["taskId"], timeout=timeout)
        result.setdefault("taskId", sub["taskId"])
        return result

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def records(self, page=1, size=20, sub_type=SUBTYPE_SYNTHESIS):
        """历史记录（``speech/urlList``）。

        返回 ``{totalCount, currentPage, pageSize, data: [...]}``。
        每行的 ``status`` 只有 **4 个取值**（1 已提交 / 2 执行中 / 3 成功 /
        4 失败）——跟 ``queryTask`` 的 7 态**不是同一套**，别混用。
        """
        return self._c.get_value("speech/urlList",
                                 {"pageNum": page, "pageSize": size,
                                  "subType": sub_type})

    def delete(self, *record_ids):
        """按**记录 id**（不是 taskId）删记录（``speech/delUrl``）。

        记录 id 在 ``task/queryTask`` 成功的 ``responseData.id`` 里，
        也在 ``speech/urlList`` 每行的 ``id`` 上。
        """
        ids = [int(i) for i in record_ids]
        if not ids:
            return None
        return self._c.get_value("speech/delUrl", {"idList": ids})

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @staticmethod
    def format_result(result):
        lines = [
            f"记录 id  : {result.get('id')}",
            f"taskId   : {result.get('taskId')}",
            f"音色     : {result.get('speaker')}",
            f"文本     : {(result.get('text') or '')[:60]}",
            f"音频地址 : {result.get('audioUrl') or '(无)'}",
        ]
        return "\n".join(lines)

    @classmethod
    def speaker_table(cls):
        rows = [f"{'参数':<14}{'说明':<18}语言", "-" * 40]
        for s in cls.SPEAKERS:
            lang = {1: "中文", 2: "英文"}.get(s.lang, s.lang)
            rows.append(f"{s.param:<14}{s.name:<16}{lang}")
        return "\n".join(rows)
