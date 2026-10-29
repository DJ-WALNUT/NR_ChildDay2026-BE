"""기관 계정 발급·수정·비밀번호 초기화."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify

from ..access import require_org, require_user_target
from ..audit import audit
from ..auth import current_user, login_required, revoke_sessions
from ..db import get_db, tx
from ..errors import ValidationError, conflict
from ..principal import current_principal
from ..security import hash_password, normalize_username, temp_password, valid_username
from ..timeutil import now
from .common import json_body

bp = Blueprint("admin_users", __name__, url_prefix="/api/admin")

_COLS = "id, username, display_name, is_active, must_change_password, last_login_at, created_at, org_id"


def _user_out(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "username": r["username"], "display_name": r["display_name"],
            "is_active": bool(r["is_active"]), "must_change_password": bool(r["must_change_password"]),
            "last_login_at": r["last_login_at"], "created_at": r["created_at"]}


def _display_name(v) -> str:
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= 40:
        raise ValidationError({"display_name": "1~40자로 입력하세요."})
    return v.strip()


@bp.get("/orgs/<int:org_id>/users")
@login_required
def list_users(org_id: int):
    require_org(org_id)
    rows = get_db().execute(f"SELECT {_COLS} FROM users WHERE org_id = ? ORDER BY id", (org_id,)).fetchall()
    return jsonify(users=[_user_out(r) for r in rows])


@bp.post("/orgs/<int:org_id>/users")
@login_required
def create_user(org_id: int):
    require_org(org_id)
    body = json_body()
    raw = body.get("username")
    username = normalize_username(raw) if isinstance(raw, str) else ""
    fields = {}
    if not valid_username(username):
        fields["username"] = "영문 소문자·숫자·._- 3~32자로 입력하세요."
    name = body.get("display_name")
    try:
        name = _display_name(name)
    except ValidationError as e:
        fields.update(e.fields)
    if fields:
        raise ValidationError(fields)
    temp = temp_password()
    with tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            conflict("username_taken")
        cur = conn.execute(
            "INSERT INTO users (org_id, username, display_name, password_hash, must_change_password,"
            " created_by, created_at) VALUES (?,?,?,?,1,?,?)",
            (org_id, username, name, hash_password(temp), current_user().id, now()))
        audit(current_principal(), "user.create", f"user:{cur.lastrowid}",
              f"계정 발급: {name}({username})", org_id=org_id)
        row = conn.execute(f"SELECT {_COLS} FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(user=_user_out(row), temp_password=temp), 201


@bp.patch("/users/<int:user_id>")
@login_required
def update_user(user_id: int):
    require_user_target(user_id)
    body = json_body()
    me = current_user()
    new_name = _display_name(body["display_name"]) if "display_name" in body else None
    active = body.get("is_active") if "is_active" in body else None
    if "is_active" in body and not isinstance(active, bool):
        raise ValidationError({"is_active": "true 또는 false여야 합니다."})
    with tx() as conn:
        t = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        p, org_id = current_principal(), t["org_id"]
        label = f"{t['display_name']}({t['username']})"
        deactivating = active is False and bool(t["is_active"])
        activating = active is True and not t["is_active"]
        if deactivating:
            if t["id"] == me.id:
                conflict("cannot_deactivate_self")
            if org_id is not None and conn.execute(
                    "SELECT COUNT(*) FROM users WHERE org_id = ? AND is_active = 1 AND id != ?",
                    (org_id, user_id)).fetchone()[0] == 0:
                conflict("last_active_user")
        if new_name is not None and new_name != t["display_name"]:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (new_name, user_id))
            audit(p, "user.update", f"user:{user_id}", f"계정 수정: {label} → {new_name}", org_id=org_id)
        if deactivating:
            conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
            revoke_sessions(user_id)
            audit(p, "user.deactivate", f"user:{user_id}", f"계정 비활성화: {label}", org_id=org_id)
        elif activating:
            conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
            audit(p, "user.activate", f"user:{user_id}", f"계정 활성화: {label}", org_id=org_id)
        row = conn.execute(f"SELECT {_COLS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(user=_user_out(row))


@bp.post("/users/<int:user_id>/reset-password")
@login_required
def reset_password(user_id: int):
    t = require_user_target(user_id)
    if t["id"] == current_user().id:
        conflict("use_password_change")
    temp = temp_password()
    with tx() as conn:
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, password_changed_at = ?"
                     " WHERE id = ?", (hash_password(temp), now(), user_id))
        revoke_sessions(user_id)
        audit(current_principal(), "user.reset_password", f"user:{user_id}",
              f"비밀번호 초기화: {t['display_name']}({t['username']})", org_id=t["org_id"])
    return jsonify(temp_password=temp)
