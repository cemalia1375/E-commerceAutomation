"""密码哈希与登录会话 token 的纯函数工具。

- 密码用 bcrypt 哈希存储。
- 登录会话 token：返回高熵随机 raw token（放 HttpOnly cookie），
  DB 只存其 sha256，避免 DB 泄露即可冒用会话。
"""
from __future__ import annotations

import hashlib
import secrets

import bcrypt


def hash_password(raw: str) -> str:
    """bcrypt 哈希明文密码，返回可直接入库的字符串。"""
    if not raw:
        raise ValueError("password must not be empty")
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。异常一律视为不匹配。"""
    if not raw or not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_token(raw_token: str) -> str:
    """对 raw session token 取 sha256（DB 存储用）。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    """生成一对 (raw_token, token_hash)。raw 下发到 cookie，hash 入库。"""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
