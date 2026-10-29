"""세션 인증, CSRF, 비밀번호 변경 의무 게이트."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import wraps

from flask import Flask, g, request

from .db import get_db
from .errors import ApiError
from .principal import Principal, set_principal
from .security import consteq, hash_password, new_token, sha256_hex
from .timeutil import now

COOKIE_NAME = "nr_session"
IDLE_SEC = 12 * 3600
ABSOLUTE_SEC = 7 * 24 * 3600
LOGIN_LIMIT = 10
LOGIN_WINDOW_SEC = 15 * 60

# CSRF 예외: 로그인만 (로그인 전에 쓰는 유일한 변경 요청)
_CSRF_EXEMPT = re.compile(r"/api/admin/auth/login")
# 비밀번호 변경 전에도 허용되는 경로
_PW_GATE_ALLOWED = re.compile(r"/api/admin/auth/(me|password|logout)")

# 타이밍 차이로 아이디 존재 여부가 드러나지 않도록 없는 계정도 같은 해시 검증을 한다
DUMMY_HASH = hash_password("dummy-password-for-timing")


@dataclass
class User:
    id: int
    username: str
    display_name: str
    org_id: int | None
    is_superadmin: bool
    must_change_password: bool


def user_json(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "org_id": u.org_id,
            "is_superadmin": u.is_superadmin, "must_change_password": u.must_change_password}


def user_principal(u: User) -> Principal:
    return Principal(kind="user", label=f"{u.display_name}({u.username})", user_id=u.id,
                     org_id=None if u.is_superadmin else u.org_id, is_superadmin=u.is_superadmin)


def start_session(user_id: int) -> tuple[str, str]:
    """세션 행을 만들고 (쿠키 값, csrf) 를 돌려준다. DB 에는 쿠키 값의 SHA-256 만 저장."""
    cookie, csrf = new_token(), new_token()
    t = now()
    get_db().execute(
        "INSERT INTO sessions (id_hash, user_id, csrf, created_at, last_seen_at, expires_at, ip, user_agent)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (sha256_hex(cookie), user_id, csrf, t, t, t + ABSOLUTE_SEC, request.remote_addr,
         (request.user_agent.string or "")[:300]),
    )
    return cookie, csrf


def revoke_sessions(user_id: int, keep_id_hash: str | None = None) -> None:
    db = get_db()
    if keep_id_hash is None:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    else:
        db.execute("DELETE FROM sessions WHERE user_id = ? AND id_hash != ?", (user_id, keep_id_hash))


def _load_session():
    """요청당 한 번 세션을 검증해 g 에 캐시한다. 만료·비활성 시 행 삭제."""
    if "auth_session" in g:
        return g.auth_session
    g.auth_session = None
    g.auth_user = None
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    db = get_db()
    id_hash = sha256_hex(cookie)
    row = db.execute(
        "SELECT s.csrf, s.last_seen_at, s.expires_at, u.id, u.username, u.display_name, u.org_id,"
        " u.is_superadmin, u.must_change_password, u.is_active AS u_active, o.is_active AS o_active"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " LEFT JOIN organizations o ON o.id = u.org_id WHERE s.id_hash = ?",
        (id_hash,),
    ).fetchone()
    if row is None:
        return None
    t = now()
    org_ok = bool(row["is_superadmin"]) or bool(row["o_active"])
    if (t >= row["expires_at"] or t - row["last_seen_at"] > IDLE_SEC
            or not row["u_active"] or not org_ok):
        db.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        return None
    db.execute("UPDATE sessions SET last_seen_at = ? WHERE id_hash = ?", (t, id_hash))
    g.auth_session = {"id_hash": id_hash, "csrf": row["csrf"]}
    g.auth_user = User(row["id"], row["username"], row["display_name"], row["org_id"],
                       bool(row["is_superadmin"]), bool(row["must_change_password"]))
    return g.auth_session


def current_user() -> User | None:
    _load_session()
    return g.auth_user


def csrf_token() -> str | None:
    s = _load_session()
    return s["csrf"] if s else None


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if current_user() is None:
            raise ApiError(401, "unauthorized")
        return f(*a, **kw)

    return wrapper


def superadmin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not current_user().is_superadmin:
            raise ApiError(403, "forbidden")
        return f(*a, **kw)

    return wrapper


def _check_csrf():
    if request.method not in ("POST", "PATCH", "PUT", "DELETE"):
        return
    if not request.path.startswith("/api/admin/") or _CSRF_EXEMPT.fullmatch(request.path):
        return
    token = csrf_token()
    if token is None:
        raise ApiError(401, "unauthorized")
    sent = request.headers.get("X-CSRF-Token", "")
    if not sent or not consteq(sent, token):
        raise ApiError(403, "csrf")


def _gate_and_principal():
    u = current_user()
    if u is None:
        return
    if (u.must_change_password and request.path.startswith("/api/admin/")
            and not _PW_GATE_ALLOWED.fullmatch(request.path)):
        raise ApiError(403, "password_change_required")
    set_principal(user_principal(u))


def init_auth(app: Flask) -> None:
    app.before_request(_check_csrf)
    app.before_request(_gate_and_principal)
