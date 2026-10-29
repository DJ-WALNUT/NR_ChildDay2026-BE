"""슈퍼관리자: 기관 관리, 백업."""

from __future__ import annotations

import io
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, send_file

from ..audit import audit
from ..auth import revoke_sessions, superadmin_required
from ..db import get_db, tx
from ..errors import ValidationError, conflict, not_found
from ..principal import current_principal
from ..timeutil import KST, now
from .admin_booths import audit_query, int_param
from .common import json_body

bp = Blueprint("super", __name__, url_prefix="/api/admin/super")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_RESERVED = {"manage", "api", "b", "p", "reserve", "check", "assets", "login"}
_COLS = "id, name, slug, is_active, created_at"


def _org_out(r: sqlite3.Row) -> dict:
    d = {"id": r["id"], "name": r["name"], "slug": r["slug"],
         "is_active": bool(r["is_active"]), "created_at": r["created_at"]}
    if "user_count" in r.keys():
        d["user_count"], d["event_count"] = r["user_count"], r["event_count"]
    return d


def _name(v) -> str:
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= 60:
        raise ValidationError({"name": "1~60자로 입력하세요."})
    return v.strip()


def _slug(v) -> str:
    if not isinstance(v, str) or not _SLUG_RE.fullmatch(v) or v in _RESERVED:
        raise ValidationError({"slug": "영문 소문자·숫자·하이픈 2~32자로 입력하세요(예약어 불가)."})
    return v


def _collect(checks) -> dict:
    """여러 필드 검증을 모아 한 번에 422 로 알린다. checks: [(key, fn, value)]"""
    out, fields = {}, {}
    for key, fn, value in checks:
        try:
            out[key] = fn(value)
        except ValidationError as e:
            fields.update(e.fields)
    if fields:
        raise ValidationError(fields)
    return out


def _bool(v) -> bool:
    if not isinstance(v, bool):
        raise ValidationError({"is_active": "true 또는 false여야 합니다."})
    return v


@bp.get("/orgs")
@superadmin_required
def list_orgs():
    rows = get_db().execute(
        "SELECT o.id, o.name, o.slug, o.is_active, o.created_at,"
        " (SELECT COUNT(*) FROM users u WHERE u.org_id = o.id) AS user_count,"
        " (SELECT COUNT(*) FROM events e WHERE e.org_id = o.id) AS event_count"
        " FROM organizations o ORDER BY o.id").fetchall()
    return jsonify(orgs=[_org_out(r) for r in rows])


@bp.post("/orgs")
@superadmin_required
def create_org():
    body = json_body()
    v = _collect([("name", _name, body.get("name")), ("slug", _slug, body.get("slug"))])
    with tx() as conn:
        if conn.execute("SELECT 1 FROM organizations WHERE slug = ?", (v["slug"],)).fetchone():
            conflict("slug_taken")
        cur = conn.execute("INSERT INTO organizations (name, slug, is_active, created_at) VALUES (?,?,1,?)",
                           (v["name"], v["slug"], now()))
        audit(current_principal(), "org.create", f"org:{cur.lastrowid}",
              f"기관 생성: {v['name']}({v['slug']})", org_id=cur.lastrowid)
        row = conn.execute(f"SELECT {_COLS} FROM organizations WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(org=_org_out(row)), 201


@bp.patch("/orgs/<int:org_id>")
@superadmin_required
def update_org(org_id: int):
    body = json_body()
    changes = _collect([(k, fn, body[k]) for k, fn in (("name", _name), ("slug", _slug), ("is_active", _bool))
                        if k in body])
    with tx() as conn:
        old = conn.execute(f"SELECT {_COLS} FROM organizations WHERE id = ?", (org_id,)).fetchone()
        if old is None:
            not_found()
        before = {"name": old["name"], "slug": old["slug"], "is_active": bool(old["is_active"])}
        changes = {k: v for k, v in changes.items() if v != before[k]}
        if "slug" in changes and conn.execute(
                "SELECT 1 FROM organizations WHERE slug = ? AND id != ?", (changes["slug"], org_id)).fetchone():
            conflict("slug_taken")
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)  # 키는 위 화이트리스트에서만 온다
            conn.execute(f"UPDATE organizations SET {sets} WHERE id = ?",
                         (*[int(v) if isinstance(v, bool) else v for v in changes.values()], org_id))
            audit(current_principal(), "org.update", f"org:{org_id}", f"기관 수정: {old['name']}",
                  org_id=org_id, detail={"before": {k: before[k] for k in changes}, "after": changes})
            if changes.get("is_active") is False:
                for u in conn.execute("SELECT id FROM users WHERE org_id = ?", (org_id,)).fetchall():
                    revoke_sessions(u["id"])
        row = conn.execute(f"SELECT {_COLS} FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return jsonify(org=_org_out(row))


@bp.get("/backup")
@superadmin_required
def backup():
    """온라인 백업 API 로 일관된 사본을 임시 파일에 만들어 메모리로 읽은 뒤 파일은 바로 지운다."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = None
    try:
        src = sqlite3.connect(str(Path(current_app.config["DATA_DIR"]) / "app.db"), timeout=10)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
        data = Path(tmp).read_bytes()
    finally:
        if src is not None:
            src.close()
        os.unlink(tmp)
    audit(current_principal(), "backup.download", None, "데이터베이스 백업 다운로드", org_id=None)
    name = f"nrbooth-backup-{datetime.now(KST):%Y%m%d-%H%M}.db"
    return send_file(io.BytesIO(data), mimetype="application/octet-stream",
                     as_attachment=True, download_name=name)


@bp.get("/audit")
@superadmin_required
def audit_log():
    return jsonify(audit_query(int_param("org_id")))
