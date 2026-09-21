# -*- coding: utf-8 -*-
"""配置：凭证的读取与写入，以及服务端地址常量。

凭证查找顺序（先到先得，见 spec Q22）::

    1. 环境变量 ``UNIPUS_AIGC_TOKEN``
    2. ``./.env``                      当前工作目录，仓库内开发用
    3. ``~/.config/unipus-aigc/.env``  用户级落盘，跨项目、与 CWD 无关

装成 plugin 之后 CWD 不再等于仓库根，所以第 3 条是常态路径；第 2 条保留是为了
兼容"就在仓库里跑"的老用法。**只查一个文件是不够的**——老实现写死
``DOTENV = ".env"``，在 plugin 场景下会静默读不到 token，然后报一句误导性的
"请在项目根目录创建 .env"。

用法::

    export UNIPUS_AIGC_TOKEN='<粘贴 userInfo.jwt>'

    # 或交给 guide skill 落盘：
    from unipus_aigc import config
    config.save_token('<JWT>')                 # -> ~/.config/unipus-aigc/.env
    config.save_token('<JWT>', scope="cwd")    # -> ./.env
"""

import base64
import hashlib
import json
import os
import time

from .errors import MissingTokenError

# ---- 服务端地址（见 docs/call-chains.md）----
#
# ⚠️ **两个域名都是对的，不是二选一、也不是笔误——不要"顺手统一"成其中一个。**
#
#   https://aigc.unipus.cn    接口文档（Confluence 55226315）里的域名。
#                             新功能按文档实现时用这个。
#   https://uaigc.unipus.cn   逆向自前端产物的域名。translate / review / kb
#                             这三条老链路是在它上面跑通的。
#
# 实测两边指向**同一个后端、同一个账号**：operation 9 的竖切在两个域名上
# 都提交成功并拿到了音频地址。差异只是入口，不是两套系统。
#
# 下面这个默认值保持逆向域名不动——老链路依赖它，改了会静默影响已跑通的功能。
# 要用文档域名时**显式覆盖**，不要改这里：
#
#     from unipus_aigc import config
#     config.API_BASE = "https://aigc.unipus.cn"
API_BASE = "https://uaigc.unipus.cn"          # 业务 API（逆向域名，见上）
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

DOTENV_CWD = ".env"                           # 相对 CWD，仅第 2 优先级用
CONFIG_SUBDIR = os.path.join(".config", "unipus-aigc")
CACHE_SUBDIR = os.path.join(".cache", "unipus-aigc")


# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
def _home(required=False):
    """用户主目录。

    ``required=True`` 时拿不到就报错——**绝不退回相对路径**，否则凭证会被写进
    仓库里（而仓库是要提交的）。
    """
    home = os.environ.get("HOME") or os.path.expanduser("~")
    if home and home != "~" and os.path.isabs(home):
        return home
    if required:
        raise MissingTokenError(
            "无法确定用户主目录（$HOME 未设置或不是绝对路径），"
            "因此定位不到 ~/.config/unipus-aigc/。"
            f"请改用环境变量 {ENV_TOKEN}，或给 save_token() 传 path= 显式指定。"
        )
    return None


def user_config_dir():
    """``~/.config/unipus-aigc``。"""
    return os.path.join(_home(required=True), CONFIG_SUBDIR)


def user_dotenv():
    """``~/.config/unipus-aigc/.env``——用户级凭证落盘位置。"""
    return os.path.join(user_config_dir(), ".env")


def cache_dir():
    """结果缓存目录；主目录不可用时返回 ``None``，调用方跳过缓存即可。"""
    home = _home()
    return os.path.join(home, CACHE_SUBDIR) if home else None


def dotenv_paths():
    """凭证**文件**的查找顺序（环境变量不在这里，它直接读 ``os.environ``）。"""
    paths = [DOTENV_CWD]
    home = _home()
    if home:
        paths.append(os.path.join(home, CONFIG_SUBDIR, ".env"))
    return paths


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------
def _load_dotenv(path):
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


def _load_all_dotenv():
    """按顺序加载**每一个**凭证文件。

    ``setdefault`` 语义保证优先级：真环境变量 > 先加载的文件 > 后加载的文件，
    即 环境变量 > ``./.env`` > ``~/.config/unipus-aigc/.env``。
    """
    for path in dotenv_paths():
        _load_dotenv(path)


def _missing_token_message():
    places = [f"环境变量 {ENV_TOKEN}"] + dotenv_paths()
    listed = "\n".join(f"    {i}. {p}" for i, p in enumerate(places, 1))
    return (
        "未找到 JWT。按优先级查过以下位置，都没有：\n"
        f"{listed}\n"
        "获取方式：登录 https://ai.unipus.cn，从浏览器 localStorage 的 "
        "userInfo.jwt 复制。\n"
        "配置方式：把 JWT 告诉 Claude，由 guide skill 调 save_token() 落盘；"
        f"或自己 export {ENV_TOKEN}='<JWT>'。"
    )


def decode_jwt(token):
    """解出 JWT 的 payload（不校验签名），用于取 openId / 过期时间。"""
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("不是合法的 JWT")
    p = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(p))


def load_token():
    """按 环境变量 -> ``./.env`` -> ``~/.config/unipus-aigc/.env`` 取 JWT。"""
    _load_all_dotenv()
    token = os.environ.get(ENV_TOKEN, "").strip()
    if not token:
        raise MissingTokenError(_missing_token_message())
    return token


def load_open_id(token=None):
    """openId 优先取环境变量，其次从 JWT 的 openId claim 解出。"""
    _load_all_dotenv()
    oid = os.environ.get(ENV_OPEN_ID, "").strip()
    if oid:
        return oid
    return decode_jwt(token or load_token()).get("openId", "")


def load_user_id(token=None):
    """v2 RAG 接口（``rag/kbp/v2/*``）要的 ``userId``。

    **它就是 JWT 里的 ``openId``**，不是另一个账号字段——所以这里只是
    :func:`load_open_id` 的别名，单列出来是为了让 v2 模块的代码读起来
    跟接口文档的字段名对得上。

    实测依据：文档自带示例用的是另一个 32 位十六进制串，但只有 ``openId``
    能被 ``v2/project/list`` 认（伪造值和 ``"1"`` 都返回 0 行）。
    """
    return load_open_id(token)


def token_expiry(token):
    """返回 token 的过期时间戳；无 exp 则返回 None。"""
    return decode_jwt(token).get("exp")


def check_token(token=None, warn_days=3):
    """检查 token 是否快过期，返回 ``(是否有效, 提示文本)``。"""
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


# ----------------------------------------------------------------------
# 写入（Q23：用户告知凭证 -> skill 落盘）
# ----------------------------------------------------------------------
def fingerprint(token):
    """token 的短指纹。用于回显"改的是哪一枚"，而不泄漏凭证本身。

    不用 ``token[:8]``：JWT 头是固定的 ``{"alg":...`` base64，前 8 位对每枚
    token 都一样，既认不出是哪枚也毫无信息量。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _chmod_quietly(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _read_token_from_file(path):
    """直接从文件读 token，**不经过 os.environ**——用于写入后的回读校验。"""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == ENV_TOKEN:
                return v.strip().strip("'\"")
    return None


def save_token(token, path=None, scope="user"):
    """把 JWT 写入落盘文件，返回写入结果。

    :param token: JWT 字符串
    :param path: 显式路径；给了就忽略 ``scope``
    :param scope: ``"user"`` -> ``~/.config/unipus-aigc/.env``（默认，跨项目）
                  ``"cwd"``  -> ``./.env``（仓库内开发用）
    :return: ``dict(path, env, fingerprint, valid, message)``

    行为保证：

    * 目录不存在则创建；文件权限 ``600``，目录 ``700``。
    * 原子写入：先写 ``.tmp`` 再 ``os.replace``，不留半截文件。
    * 已有 ``UNIPUS_AIGC_TOKEN=`` 行则**替换该行**，不会追加出第二条。
    * 覆盖前若旧值与新值不同，先把旧文件备份成 ``<path>.bak``（同样 ``600``），
      这样传错 token 把能用的凭证冲掉时还能恢复。
    * 写完**直接从文件回读校验**（不经过 ``os.environ``，否则 ``setdefault``
      会把写入失败掩盖掉）。
    * **返回值不含 token 本身**，只有 sha256 指纹。任何情况下都不把 JWT
      打进 stdout / 日志 / 异常消息。

    写入成功后会把 ``os.environ[ENV_TOKEN]`` 也设成新值，让**当前进程**立刻
    用上新凭证；新起的进程仍按优先级重新解析（真环境变量依然最高）。
    """
    token = (token or "").strip()
    if not token:
        raise ValueError("token 不能为空")
    decode_jwt(token)                     # 格式校验：不是合法 JWT 就别落盘

    if path is None:
        if scope == "user":
            path = user_dotenv()
        elif scope == "cwd":
            path = DOTENV_CWD
        else:
            raise ValueError(f"scope 只能是 'user' 或 'cwd'，收到 {scope!r}")

    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
        _chmod_quietly(directory, 0o700)

    # 覆盖前备份旧值：save_token 是静默替换，传错 token 会冲掉本来能用的凭证。
    previous = _read_token_from_file(path)
    backed_up = None
    if previous and previous != token:
        backed_up = path + ".bak"
        _write_atomic(backed_up, f"{ENV_TOKEN}={previous}\n")

    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    replaced = False
    for i, line in enumerate(lines):
        if "=" in line and line.split("=", 1)[0].strip() == ENV_TOKEN:
            lines[i] = f"{ENV_TOKEN}={token}"
            replaced = True
    if not replaced:
        lines.append(f"{ENV_TOKEN}={token}")

    _write_atomic(path, "\n".join(lines) + "\n")

    if _read_token_from_file(path) != token:
        raise MissingTokenError(f"写入 {path} 后回读校验失败")

    os.environ[ENV_TOKEN] = token

    valid, message = check_token(token)
    return {
        "path": path,
        "env": ENV_TOKEN,
        "fingerprint": fingerprint(token),
        "previous_fingerprint": fingerprint(previous) if previous else None,
        "backup": backed_up,
        "valid": valid,
        "message": message,
    }


def _write_atomic(path, text):
    """原子写入 + 600 权限。先写 ``.tmp`` 再 ``os.replace``，不留半截文件。"""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    _chmod_quietly(path, 0o600)           # umask 可能盖过 os.open 的 mode，兜一次
