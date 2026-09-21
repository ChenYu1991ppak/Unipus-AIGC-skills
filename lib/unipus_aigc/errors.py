# -*- coding: utf-8 -*-
"""异常类型。"""


class AigcError(Exception):
    """平台返回 ``success=false`` 或 HTTP 非 200。"""

    def __init__(self, message, *, code=None, path=None, payload=None):
        super().__init__(message)
        self.code = code
        self.path = path
        self.payload = payload

    def __str__(self):
        bits = [super().__str__()]
        if self.path:
            bits.append(f"path={self.path}")
        if self.code is not None:
            bits.append(f"code={self.code}")
        return " | ".join(bits)


class TaskFailed(AigcError):
    """异步任务到达了**失败类终态**。

    ``queryTask`` 的 7 个 status 里有三个会走到这里：

    * ``4`` 执行失败
    * ``5`` 解析异常
    * ``7`` 已取消

    ``6``（排队中）**不是**失败——它跟 ``1``/``2`` 一样属于"还在跑"，
    调用方应当继续轮询或退出码 3。
    """


class TaskTimeout(AigcError):
    """异步任务在给定时间内没有到达终态。"""


class MissingTokenError(AigcError):
    """三处凭证位置都没找到 JWT。

    单独一个类型是为了让 CLI 能给出"去哪儿拿、怎么配"的干净提示，
    而不是甩一个 RuntimeError traceback。
    """


class StillRunning(TaskTimeout):
    """长任务还没到终态，我们停止等待了。

    这**不是错误**：状态在平台侧，稍后用同一个 id 再 poll 一次即可。
    CLI 把它映射成退出码 3，与真失败（退出码 1）区分开。

    继承 :class:`TaskTimeout` 是为了向后兼容——老代码里 ``except TaskTimeout``
    的地方仍然抓得到它。
    """
