"""로그인, 로그아웃, 내 정보, 비밀번호 변경."""

from __future__ import annotations

from flask import Blueprint, Response, current_app, g, jsonify, request

from .. import ratelimit
from ..audit import audit
from ..auth import (COOKIE_NAME, DUMMY_HASH, LOGIN_LIMIT, LOGIN_WINDOW_SEC, User, csrf_token,
                    current_user, login_required, revoke_sessions, start_session, user_json,
                    user_principal)
from ..db import get_db, tx
from ..errors import ApiError, ValidationError
from ..security import hash_password, normalize_username, password_problem, sha256_hex, verify_password
from ..timeutil import now
from .common import json_body

bp = Blueprint("auth", __name__, url_prefix="/api/admin/auth")


def _cookie_args() -> dict:
    return dict(httponly=True, secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax", path="/")


@bp.post("/login")
def login():
    body = json_body()
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise ApiError(401, "invalid_credentials")
    username = normalize_username(username)
    key = f"login:{username}:{request.remote_addr}"
    if ratelimit.exceeded(key, LOGIN_LIMIT, LOGIN_WINDOW_SEC):
        raise ApiError(429, "rate_limited")
    db = get_db()
    row = db.execute(
        "SELECT u.*, o.is_active AS o_active FROM users u"
        " LEFT JOIN organizations o ON o.id = u.org_id WHERE u.username = ?", (username,)
    ).fetchone()
    ok = verify_password(row["password_hash"] if row else DUMMY_HASH, password)
    usable = (row is not None and ok and row["is_active"]
              and (row["is_superadmin"] or row["o_active"]))
    if not usable:
        ratelimit.hit(key)
        raise ApiError(401, "invalid_credentials")
    user = User(row["id"], row["username"], row["display_name"], row["org_id"],
                bool(row["is_superadmin"]), bool(row["must_change_password"]))
    old = request.cookies.get(COOKIE_NAME)
    with tx() as conn:
        if old:  # 세션 고정 방지: 기존 세션 교체
            conn.execute("DELETE FROM sessions WHERE id_hash = ?", (sha256_hex(old),))
        cookie, csrf = start_session(user.id)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), user.id))
        audit(user_principal(user), "auth.login", f"user:{user.id}", "로그인", org_id=user.org_id)
    ratelimit.clear(key)
    resp = jsonify(user=user_json(user), csrf=csrf)
    resp.set_cookie(COOKIE_NAME, cookie, **_cookie_args())
    return resp


@bp.post("/logout")
@login_required
def logout():
    get_db().execute("DELETE FROM sessions WHERE id_hash = ?", (g.auth_session["id_hash"],))
    resp = Response(status=204)
    resp.delete_cookie(COOKIE_NAME, **_cookie_args())
    return resp


@bp.get("/me")
@login_required
def me():
    u = current_user()
    org = None
    if u.org_id is not None:
        r = get_db().execute("SELECT id, name, slug FROM organizations WHERE id = ?", (u.org_id,)).fetchone()
        org = dict(r) if r else None
    return jsonify(user=user_json(u), csrf=csrf_token(), org=org)


@bp.post("/password")
@login_required
def change_password():
    u = current_user()
    body = json_body()
    current, new = body.get("current"), body.get("new")
    key = f"login:{u.username}:{request.remote_addr}"
    if ratelimit.exceeded(key, LOGIN_LIMIT, LOGIN_WINDOW_SEC):
        raise ApiError(429, "rate_limited")
    row = get_db().execute("SELECT password_hash FROM users WHERE id = ?", (u.id,)).fetchone()
    if not isinstance(current, str) or not verify_password(row["password_hash"], current):
        ratelimit.hit(key)
        # 401 은 "세션 없음"으로 해석되므로 쓰지 않는다
        raise ValidationError({"current": "현재 비밀번호가 일치하지 않습니다."})
    problem = "필수 항목입니다." if not isinstance(new, str) else password_problem(new, u.username)
    if problem:
        raise ValidationError({"new": problem})
    with tx() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, password_changed_at = ? WHERE id = ?",
            (hash_password(new), now(), u.id),
        )
        revoke_sessions(u.id, keep_id_hash=g.auth_session["id_hash"])
        audit(user_principal(u), "auth.password_change", f"user:{u.id}", "비밀번호 변경", org_id=u.org_id)
    ratelimit.clear(key)
    return Response(status=204)
