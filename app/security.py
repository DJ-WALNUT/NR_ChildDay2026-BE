"""비밀번호·토큰·해시 도구."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_ph = PasswordHasher()  # 기본값 = Argon2id


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(stored: str, pw: str) -> bool:
    # 불일치·형식 오류 모두 False
    try:
        return _ph.verify(stored, pw)
    except (VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hmac_hex(key: bytes, data: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


MIN_PASSWORD = 8
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")


def link_token() -> str:
    return secrets.token_urlsafe(18)


def temp_password() -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(10))


def normalize_username(s: str) -> str:
    return s.strip().lower()


def valid_username(s: str) -> bool:
    return bool(_USERNAME_RE.match(s))


def password_problem(pw: str, username: str) -> str | None:
    if len(pw) < MIN_PASSWORD:
        return "8자 이상이어야 합니다."
    if pw.lower() == username.lower():
        return "아이디와 같을 수 없습니다."
    return None
