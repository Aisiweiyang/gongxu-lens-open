"""试点应用认证：pbkdf2 口令哈希、会话、CSRF 双提交。

- 账号没有硬编码默认密码：首次启动走初始化管理员流程（仅当 users 为空时可用）。
- 写入接口要求会话 + CSRF token 双校验；服务端校验角色权限，不只隐藏按钮。
- 口令哈希：pbkdf2_hmac(sha256, 200_000 轮, 16 字节盐)；会话令牌 secrets.token_urlsafe。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

PBKDF2_ITERATIONS = 200_000
SESSION_TTL_SECONDS = 12 * 3600
ROLES = ("admin", "editor", "viewer")
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), PBKDF2_ITERATIONS).hex()
    return digest, salt


def verify_password(password, salt, expected_digest):
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, expected_digest)


def new_session_token():
    return secrets.token_urlsafe(32)


def new_csrf_token():
    return secrets.token_urlsafe(32)


def session_expiry():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + SESSION_TTL_SECONDS))


def password_policy_error(password):
    """口令策略约束：至少 8 位，且同时包含字母与数字（API 与 CLI 共用）。"""
    import re
    if len(password) < 8:
        return "密码至少 8 位"
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "密码需同时包含字母与数字"
    return None


def require_role(role, minimum):
    """角色比较：viewer < editor < admin。"""
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 99)
