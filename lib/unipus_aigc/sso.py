# -*- coding: utf-8 -*-
"""账号密码登录 unipus SSO，换取 AIGC 平台的 JWT——凭证的**自动续期**。

本仓库原来只能"手动从浏览器 localStorage 粘一枚 JWT"，48 小时后失效、再粘一次。
这个模块把它升级成：**第一次给账号密码，之后自动续。**

链路（全部实测跑通，见 `内部的接口记录` §11）::

    (1) POST {SSO}/sso/0.1/sso/cip/login
          {username: AES_HEX(账号), password: AES_HEX(密码),
           remember: true, agreement: true, service: https://ai.unipus.cn}
          -> rs.serviceTicket = "ST-…"

    (2) GET  {SSO}/sso/serviceTicket/validate?service=…&ticket=ST-…
          -> rs = {jwt, rt, jwtExpire, rtExpire, effectiveTime, links}

    (3) POST {SSO}/sso/4.0/sso/refresh_jwt   {rt: "…"}
          -> 同样回一整套 rs（jwt 换新、**rt 不轮换**）

.. warning::
   **第 (3) 步是"前端真实在用"那条，不是猜的。** 它在 ``aigc_index.js`` 里::

       Qs.post(`${SSOURL}/sso/4.0/sso/refresh_jwt`, e)
       // 触发条件：jwtExpire 前 60 秒；若 rtExpire 已过则抛「登录已过期」

   实测第 (3) 步 code=0、新 jwt 能直接打业务接口；``rt`` 乱写回
   ``20001 refresh token过期``。

   注：早先有人从 ``usso.min.js`` 里读不出 refresh 接口，就下了"unipus SSO 无
   独立 refresh"的结论——**那个结论是错的**，接口在 AIGC 那个包（``aigc_index.js``）
   里，不在 SSO 自己的包里。

.. warning::
   加密用的 AES key / IV 是**公开常量**，来自 ``usso.min.js``::

       enc.Hex.parse("8AD70B641C024C7ADA2ECD082EC0334F")   // 16 字节
       Uint8Array([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]) // IV
       AES.encrypt(utf8, key, {iv, mode: CBC, padding: Pkcs7}).toUpperCase()

   已核对线上 ``birdflock.unipus.cn/rel/fe/usso/1.12.1/usso.min.js``，
   **一字不差**。所以"账号密码传输加密"这一层**没有秘密**——它只保证不明文，
   不是认证。拿到这把 key 并不能绕开密码。

.. warning::
   **密码落盘了**（``UNIPUS_AIGC_PASSWORD_ENC``），这是用户明确选择的方案。
   加密是 AES-128-CBC + HMAC-SHA256（encrypt-then-MAC），密钥由
   ``UNIPUS_AIGC_SECRET`` 派生。**但要说实话**：默认情况下那个 secret 就放在
   同一个目录的 ``secret`` 文件里，所以这层加密挡的是——

   * ``grep`` / 日志 / 截图里的意外暴露；
   * ``.env`` 被单独备份、分享、误提交。

   **挡不住**能读你 home 目录的进程。想真的隔开，就把 ``UNIPUS_AIGC_SECRET``
   放到环境变量里（比如从钥匙串注入），别让它落在磁盘上。
"""

import base64
import hashlib
import hmac
import os
import time

from . import config
from .errors import AigcError

# ----------------------------------------------------------------------
# 常量（全部实测确认）
# ----------------------------------------------------------------------
#: SSO 根地址。**不是**业务域名，也不是前端域名。
SSO_BASE = "https://sso.unipus.cn"

#: 登录：密码换 ticket。
SSO_LOGIN_PATH = "/sso/0.1/sso/cip/login"

#: ticket 换 JWT。
SSO_VALIDATE_PATH = "/sso/serviceTicket/validate"

#: rt 换新 JWT（前端在 ``aigc_index.js`` 里用的那条）。
SSO_REFRESH_PATH = "/sso/4.0/sso/refresh_jwt"

#: 传给 SSO 的 ``service`` 参数。**ticket 与它绑定，必须与抓包一致**，
#: 换一个值 ticket 就验不过。
SERVICE_URL = "https://ai.unipus.cn"

#: 登录表单加密的 key / IV——**公开常量**，来自 ``usso.min.js``。
AES_KEY_HEX = "8AD70B641C024C7ADA2ECD082EC0334F"
AES_IV_HEX = "0102030405060708090A0B0C0D0E0F10"

#: 提前多少秒认为 JWT 该续了。前端用的是 60 秒（``dse = 60*1000``）。
REFRESH_MARGIN_SECONDS = 300

#: 没给 ``jwtExpire`` 时的保守估算（实测 exp-iat = 48.0 小时）。
DEFAULT_JWT_TTL = 48 * 3600


def _timeout():
    return getattr(config, "SSO_TIMEOUT", 30)


class SsoError(AigcError):
    """SSO 链路上的错误（登录失败 / ticket 验不过 / rt 过期）。"""


# ======================================================================
# 一、AES-128-CBC + PKCS7（纯标准库，无外部依赖）
# ======================================================================
#
# 为什么自己写：这个仓库的依赖里没有 pycryptodome / cryptography，而加密只用在
# "登录表单"这一处、每次登录一个块，性能完全不敏感。为它引一个 C 扩展依赖
# 不划算，所以这里放一份纯标准库实现。
#
# **正确性**用 FIPS-197 官方测试向量自测（见 tests 段落与 :func:`_self_check`）：
#     key = 000102…0f, pt = 00112233445566778899aabbccddeeff
#     ct  = 69c4e0d86a7b0430d8cdb78070b4c55a

_SBOX = []
_INV_SBOX = [0] * 256


def _init_tables():
    """按 FIPS-197 构造 S-box（用 GF(2^8) 的乘法逆 + 仿射变换）。"""
    p = q = 1
    sbox = [0] * 256
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
              ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    _SBOX.extend(sbox)
    for i, v in enumerate(sbox):
        _INV_SBOX[v] = i


_init_tables()


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _gmul(a, b):
    """GF(2^8) 乘法。"""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _expand_key(key):
    """密钥扩展。``key`` 必须是 16 字节（AES-128）。"""
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _encrypt_block(block, w, nr):
    """AES-128 单块加密（16 字节）。"""
    s = [[block[4 * c + r] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(0)
    for rnd in range(1, nr + 1):
        for r in range(4):
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != nr:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
                s[3][c] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
        add_round_key(rnd)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _cbc_encrypt(key, iv, data):
    w, nr = _expand_key(key)
    out = b""
    prev = bytes(iv)
    for i in range(0, len(data), 16):
        blk = bytes(x ^ y for x, y in zip(data[i:i + 16], prev))
        enc = _encrypt_block(blk, w, nr)
        out += enc
        prev = enc
    return out


def _pkcs7(data, size=16):
    n = size - len(data) % size
    return data + bytes([n]) * n


def _unpkcs7(data, size=16):
    if not data or len(data) % size:
        raise SsoError("密文长度不是块大小的整数倍")
    n = data[-1]
    if not 1 <= n <= size or data[-n:] != bytes([n]) * n:
        raise SsoError("PKCS7 填充非法")
    return data[:-n]


def encrypt_sso(plaintext):
    """登录表单用的加密：AES-128-CBC + PKCS7 → **大写 HEX**。

    与前端 ``encryptAsSSO`` 逐字节一致（key/IV 是公开常量）。
    """
    key = bytes.fromhex(AES_KEY_HEX)
    iv = bytes.fromhex(AES_IV_HEX)
    ct = _cbc_encrypt(key, iv, _pkcs7(plaintext.encode("utf-8")))
    return ct.hex().upper()


def _self_check():
    """FIPS-197 测试向量自测。密钥扩展/轮函数写错了这里会立刻炸。

    放在模块级跑一次（约 1ms），**不依赖任何外部工具**——比"信任这份实现"稳。
    """
    key = bytes(range(16))
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    got = _cbc_encrypt(key, bytes(16), pt)
    want = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    if got != want:
        raise SsoError(
            f"AES 自测失败：得到 {got.hex()}，期望 {want.hex()}。"
            "这份 AES 实现不可用，登录加密会出错——请勿继续。")


_self_check()


# ======================================================================
# 二、密码落盘用的对称加密（AES-128-CBC + HMAC-SHA256，encrypt-then-MAC）
# ======================================================================
#
# 注意这跟上面那个 ``encrypt_sso`` **不是一回事**：
#
#   encrypt_sso   —— 发往 SSO 的登录表单，key 是公开常量，**不是保密**
#   encrypt_at_rest —— 存本地的密码，key 来自用户自己的 UNIPUS_AIGC_SECRET

def _derive(secret, purpose):
    """从一个 secret 派生出用途隔离的子密钥。"""
    return hashlib.sha256(f"{purpose}:{secret}".encode("utf-8")).digest()


def encrypt_at_rest(plaintext, secret):
    """加密后返回 base64(``iv`` + ``ct`` + ``mac``)。"""
    if not plaintext:
        return ""
    enc_key = _derive(secret, "enc")[:16]
    mac_key = _derive(secret, "mac")
    iv = os.urandom(16)
    ct = _cbc_encrypt(enc_key, iv, _pkcs7(plaintext.encode("utf-8")))
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()[:16]
    return base64.b64encode(iv + ct + mac).decode("ascii")


def decrypt_at_rest(blob, secret):
    """解密 :func:`encrypt_at_rest` 的产物。**MAC 不过就抛**，不返回垃圾。"""
    if not blob:
        return ""
    try:
        raw = base64.b64decode(blob)
    except Exception as e:
        raise SsoError(f"密文不是合法 base64：{e}")
    if len(raw) < 32:
        raise SsoError("密文太短")
    iv, ct, mac = raw[:16], raw[16:-16], raw[-16:]
    mac_key = _derive(secret, "mac")
    want = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, want):
        raise SsoError(
            "密文校验失败（MAC 不匹配）——UNIPUS_AIGC_SECRET 变了，"
            "或者文件被改过。请重新 `run.sh sso login`。")
    enc_key = _derive(secret, "enc")[:16]
    return _unpkcs7(_aes_cbc_decrypt(enc_key, iv, ct)).decode("utf-8")


def _aes_cbc_decrypt(key, iv, data):
    """AES-128-CBC 解密（只给本地存储用——登录那侧是单向的，不需要解密）。"""
    w, nr = _expand_key(key)
    out = b""
    prev = bytes(iv)
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        pt = _decrypt_block(blk, w, nr)
        out += bytes(x ^ y for x, y in zip(pt, prev))
        prev = blk
    return out


def _decrypt_block(block, w, nr):
    """AES-128 单块解密（InvShiftRows / InvSubBytes / InvMixColumns / AddRoundKey）。"""
    s = [[block[4 * c + r] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(nr)
    for rnd in range(nr - 1, -1, -1):
        # InvShiftRows：第 r 行右移 r 位
        for r in range(1, 4):
            s[r] = s[r][-r:] + s[r][:-r]
        # InvSubBytes
        for r in range(4):
            for c in range(4):
                s[r][c] = _INV_SBOX[s[r][c]]
        add_round_key(rnd)
        # InvMixColumns（最后一轮不做）
        if rnd != 0:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = (_gmul(a[0], 14) ^ _gmul(a[1], 11)
                           ^ _gmul(a[2], 13) ^ _gmul(a[3], 9))
                s[1][c] = (_gmul(a[0], 9) ^ _gmul(a[1], 14)
                           ^ _gmul(a[2], 11) ^ _gmul(a[3], 13))
                s[2][c] = (_gmul(a[0], 13) ^ _gmul(a[1], 9)
                           ^ _gmul(a[2], 14) ^ _gmul(a[3], 11))
                s[3][c] = (_gmul(a[0], 11) ^ _gmul(a[1], 13)
                           ^ _gmul(a[2], 9) ^ _gmul(a[3], 14))
    return bytes(s[r][c] for c in range(4) for r in range(4))



# ======================================================================
# 三、SSO 三步网络链路
# ======================================================================
def _http(requests=None):
    if requests is None:
        import requests as _r
        return _r
    return requests


def _post_json(requests, url, payload, timeout):
    resp = requests.post(url, json=payload, timeout=timeout, headers={
        "Content-Type": "application/json",
        "Origin": SERVICE_URL,
        "Referer": SERVICE_URL + "/",
    })
    if resp.status_code != 200:
        raise SsoError(f"HTTP {resp.status_code}", code=resp.status_code,
                       path=url, payload=resp.text[:400])
    try:
        return resp.json()
    except ValueError:
        raise SsoError("响应不是 JSON", path=url, payload=resp.text[:400])


def _rs_of(body):
    """SSO 的响应体把数据放在 ``rs``（不是 ``data``）。两个都认。"""
    if not isinstance(body, dict):
        raise SsoError("响应不是 JSON 对象", payload=str(body)[:300])
    code = str(body.get("code", ""))
    if code and code not in ("0", "200"):
        raise SsoError(body.get("msg") or body.get("error") or "SSO 返回失败",
                       code=body.get("code"), payload=body)
    rs = body.get("rs")
    if rs is None:
        rs = body.get("data")
    if not isinstance(rs, dict):
        raise SsoError("SSO 响应里没有 rs", payload=str(body)[:300])
    return rs


def login(account, password, *, requests=None, timeout=None):
    """完整登录：账号密码 → ``{jwt, rt, jwtExpire, rtExpire, ...}``。

    :raises SsoError: 账号密码错、ticket 验不过、或响应结构不认识。

    .. warning::
       调用方拿到返回值后**应当只保留 ``rt``**——``password`` 是明文参数，
       不要把它存进任何返回结构里。
    """
    requests = _http(requests)
    timeout = timeout or _timeout()

    # (1) 密码换 ticket
    body = {"username": encrypt_sso(account), "password": encrypt_sso(password),
            "remember": True, "agreement": True, "service": SERVICE_URL}
    rs1 = _rs_of(_post_json(requests, SSO_BASE + SSO_LOGIN_PATH, body, timeout))
    ticket = rs1.get("serviceTicket") or rs1.get("ticket") or ""
    if not ticket:
        raise SsoError("登录成功但没拿到 serviceTicket",
                       payload={k: v for k, v in rs1.items() if k != "jwt"})

    # (2) ticket 换 JWT
    resp = requests.get(SSO_BASE + SSO_VALIDATE_PATH, timeout=timeout,
                        params={"service": SERVICE_URL, "ticket": ticket},
                        headers={"Origin": SERVICE_URL,
                                 "Referer": SERVICE_URL + "/"})
    if resp.status_code != 200:
        raise SsoError(f"HTTP {resp.status_code}", code=resp.status_code,
                       path=SSO_VALIDATE_PATH, payload=resp.text[:400])
    try:
        rs2 = _rs_of(resp.json())
    except ValueError:
        raise SsoError("validate 响应不是 JSON", payload=resp.text[:400])

    jwt = rs2.get("jwt") or rs2.get("token") or ""
    if not jwt:
        raise SsoError("ticket 验证通过但没拿到 jwt",
                       payload={k: v for k, v in rs2.items() if k != "rt"})
    return _pair(rs2)


def refresh(rt, *, requests=None, timeout=None):
    """用 ``rt`` 换一对新的 ``{jwt, rt, ...}``。

    .. warning::
       **端点来自 ``aigc_index.js``，不是 ``usso.min.js``** —— 早先有人只看
       后者，下了"SSO 无 refresh 接口"的结论，那是错的。

       实测 ``rt`` **不轮换**（换回来的 ``rt`` 跟传进去的一样），
       所以同一个 rt 可以反复用，直到 ``rtExpire``。
    """
    requests = _http(requests)
    timeout = timeout or _timeout()
    rs = _rs_of(_post_json(requests, SSO_BASE + SSO_REFRESH_PATH,
                           {"rt": rt}, timeout))
    jwt = rs.get("jwt") or ""
    if not jwt:
        raise SsoError("refresh 成功但没拿到 jwt",
                       payload={k: v for k, v in rs.items() if k != "rt"})
    return _pair(rs)


def _as_int(v):
    """``rs`` 里那些时间字段实测是 ``str``（``"1790167672"``），不是 int。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _rs_int(rs, *names):
    """从 ``rs`` 里按候选名取一个整数时间戳（逐个试，空值跳过）。

    这个函数存在的原因是**踩过一次**：早先写的是
    ``rs.get("jwtExpire")`` 直接取，而 SSO 回的键是
    ``jwtExpire`` / ``rtExpire`` —— 名字对得上，但值是**字符串**，
    于是下游 ``if jwt_expire:`` 判的是真值、``str(int(...))`` 却没做，
    写进 ``.env`` 的是一串字符串时间戳，续期判断全错。
    候选名一起试 + 统一转 int，两边都兜住。
    """
    for name in names:
        got = _as_int(rs.get(name))
        if got:
            return got
    return None


def _pair(rs):
    """把 SSO 返回的 ``rs`` 归一成我们内部的命名。

    实测 ``rs`` 的形状（``validate`` 与 ``refresh_jwt`` 一致）::

        {jwt, rt, jwtExpire, rtExpire, effectiveTime, links}
        jwt            828/861 字符
        rt             96 字符
        jwtExpire      秒级 unix 时间戳（**字符串**）
        rtExpire       秒级 unix 时间戳（**字符串**）
    """
    return {
        "jwt": rs.get("jwt") or rs.get("token") or "",
        "rt": rs.get("rt") or rs.get("refreshToken") or "",
        "jwtExpire": _rs_int(rs, "jwtExpire", "jwt_expire"),
        "rtExpire": _rs_int(rs, "rtExpire", "rt_expire"),
        "effectiveTime": _rs_int(rs, "effectiveTime", "effective_time"),
    }


# ======================================================================
# 四、续期决策（纯函数，不碰网络也不碰磁盘）
# ======================================================================
def needs_refresh(jwt, *, now=None, margin=REFRESH_MARGIN_SECONDS, jwt_expire=None):
    """这个 JWT 现在该不该续？

    * 没有 jwt → 该续（还没登录过）。
    * 有 jwt 但解析不出 exp → **不续**（保守：能用的就别动）。
    * ``exp - now <= margin`` → 该续。

    ``jwt_expire`` 可以直接给（从落盘的 ``UNIPUS_AIGC_JWT_EXPIRE`` 来），
    免得每次都要解 JWT。
    """
    if not jwt:
        return True
    now = now if now is not None else time.time()
    exp = jwt_expire
    if not exp:
        try:
            exp = config.token_expiry(jwt)
        except Exception:
            return False
    if not exp:
        return False
    return (exp - now) <= margin


def rt_alive(rt_expire, *, now=None):
    """``rt`` 还在有效期内吗？没有 ``rtExpire`` 时按"还活着"处理（不知道就别拦）。"""
    if not rt_expire:
        return True
    return (now if now is not None else time.time()) < rt_expire
