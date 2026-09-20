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
    """异步任务终态为失败（status=4）。"""


class TaskTimeout(AigcError):
    """异步任务在给定时间内没有到达终态。"""
