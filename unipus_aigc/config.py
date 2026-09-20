# -*- coding: utf-8 -*-
"""配置：从环境变量 / .env 读取，不把凭证写进代码。

用法::

    export UNIPUS_AIGC_TOKEN='<粘贴 userInfo.jwt>'
    # 或把 JWT 写进项目根目录的 .env（已在 .gitignore 中）
"""

import base64
import json
import os
import time

# ---- 服务端地址（逆向自前端产物，见 docs/call-chains.md）----
API_BASE = "https://uaigc.unipus.cn"          # 业务 API
WS_URL = "wss://umcs.unipus.cn"               # socket.io，用于拿 socketId
WS_PATH = "/umcs"                             # socket.io path
WS_NAMESPACE = "/aigc"
WS_EVENT = "msg_aigc"
WS_APP_ID = "1200"                            # 前端硬编码的 appId
WS_DEV_ID = "aigc"                            # 前端硬编码的 devId
QINIU_UPLOAD_HOST = "https://up-z1.qiniup.com"  # birdflock bucket 在华北(z1)
SOURCE = "20106"                              # axios 拦截器里硬编码的 source

ENV_TOKEN = "UNIPUS_AIGC_TOKEN"
ENV_OPEN_ID = "UNIPUS_AIGC_OPEN_ID"
DOTENV = ".env"


def _load_dotenv(path=DOTENV):
    """极简 .env 解析：只处理 KEY=VALUE，已存在的环境变量优先。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            os.environ.setdefault(k, v)


def decode_jwt(token):
    """解出 JWT 的 payload（不校验签名），用于取 openId / 过期时间。"""
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("不是合法的 JWT")
    p = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(p))


def load_token():
    """按 环境变量 -> .env 的顺序取 JWT；都没有则抛错。"""
    _load_dotenv()
    token = os.environ.get(ENV_TOKEN, "").strip()
    if not token:
        raise RuntimeError(
            f"未找到 JWT。请设置环境变量 {ENV_TOKEN}，或在项目根目录创建 .env "
            f"写入 {ENV_TOKEN}=<userInfo.jwt>。\n"
            "获取方式：登录 https://ai.unipus.cn，从 localStorage 的 userInfo.jwt 复制。"
        )
    return token


def load_open_id(token=None):
    """openId 优先取环境变量，其次从 JWT 的 openId claim 解出。"""
    _load_dotenv()
    oid = os.environ.get(ENV_OPEN_ID, "").strip()
    if oid:
        return oid
    return decode_jwt(token or load_token()).get("openId", "")


def token_expiry(token):
    """返回 token 的过期时间戳；无 exp 则返回 None。"""
    return decode_jwt(token).get("exp")


def check_token(token=None, warn_days=3):
    """检查 token 是否快过期，返回 (是否有效, 提示文本)。"""
    token = token or load_token()
    exp = token_expiry(token)
    if not exp:
        return True, "token 无 exp 字段，无法判断有效期"
    left = exp - time.time()
    if left <= 0:
        return False, "token 已过期，请重新登录 ai.unipus.cn 获取"
    days = left / 86400
    if days < warn_days:
        return True, f"token 将在 {days:.1f} 天后过期，注意及时更换"
    return True, f"token 有效，剩余 {days:.1f} 天"
