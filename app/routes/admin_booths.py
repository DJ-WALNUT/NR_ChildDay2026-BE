"""부스 관리 (계정 로그인 기준)."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request

from .. import timeutil
from ..access import BOOTH_SQL, load_booth, require_booth, require_event, require_org
from ..audit import audit, list_audit
from ..auth import login_required
from ..booth_ops import (DEFAULTS, SETTING_FIELDS, apply_settings, booth_json, clear_reservations,
                         delete_reservation, export, list_reservations, parse_booth_ids, parse_stats_mode, set_status,
                         stats, toggle, validate_settings)
from ..db import get_db, tx
from ..errors import ValidationError, not_found
from ..principal import current_principal
from ..security import link_token
from ..timeutil import now
from .common import json_body

bp = Blueprint("admin_booths", __name__, url_prefix="/api/admin")

MAX_BULK = 500


def _is_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_partner(partner_id, event_id: int):
    if partner_id is None:
        return None
    ok = _is_id(partner_id) and get_db().execute(
        "SELECT 1 FROM partners WHERE id = ? AND event_id = ?", (partner_id, event_id)).fetchone()
    if not ok:
        raise ValidationError({"partner_id": "이 행사의 운영 단체여야 합니다."})
    return partner_id


def _clean_new(body: dict, event_id: int) -> tuple[dict, int | None]:
    """생성용 검증. 누락 필드는 기본값. name 필수."""
    errors: dict[str, str] = {}
    clean: dict = {}
    try:
        clean = validate_settings({**DEFAULTS, **{k: body[k] for k in SETTING_FIELDS if k in body}},
                                  base=DEFAULTS)
    except ValidationError as e:
        errors.update(e.fields)
    partner = None
    try:
        partner = _check_partner(body.get("partner_id"), event_id)
    except ValidationError as e:
        errors.update(e.fields)
    if errors:
        raise ValidationError(errors)
    return clean, partner


def _insert(conn: sqlite3.Connection, event_id: int, clean: dict, partner_id: int | None) -> int:
    cols = ("event_id", "partner_id", *SETTING_FIELDS, "manage_token", "created_at")
    vals = (event_id, partner_id, *(clean[k] for k in SETTING_FIELDS), link_token(), now())
    cur = conn.execute(f"INSERT INTO booths ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
    return cur.lastrowid


@bp.get("/events/<int:event_id>/booths")
@login_required
def list_booths(event_id: int):
    require_event(event_id)
    rows = get_db().execute(
        "SELECT id FROM booths WHERE event_id = ? ORDER BY created_at DESC, id DESC", (event_id,)).fetchall()
    return jsonify(booths=[booth_json(load_booth(r["id"])) for r in rows])


@bp.post("/events/<int:event_id>/booths")
@login_required
def create_booth(event_id: int):
    ev = require_event(event_id)
    clean, partner = _clean_new(json_body(), event_id)
    with tx() as conn:
        bid = _insert(conn, event_id, clean, partner)
        audit(current_principal(), "booth.create", f"booth:{bid}", f"부스 생성: {clean['name']}",
              org_id=ev["org_id"], detail={"mode": clean["mode"], "partner_id": partner})
    return jsonify(booth=booth_json(load_booth(bid))), 201


@bp.post("/events/<int:event_id>/booths/bulk")
@login_required
def bulk_create(event_id: int):
    ev = require_event(event_id)
    rows = json_body().get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BULK:
        raise ValidationError({"rows": f"1~{MAX_BULK}개의 행이 필요합니다."})
    cleaned, bad = [], {}
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValidationError({"row": "올바르지 않은 행입니다."})
            cleaned.append(_clean_new(row, event_id))
        except ValidationError as e:
            bad[str(i)] = next(iter(e.fields.values()))
    if bad:
        raise ValidationError({"rows": bad})
    with tx() as conn:
        for clean, partner in cleaned:
            _insert(conn, event_id, clean, partner)
        audit(current_principal(), "booth.bulk_create", f"event:{event_id}",
              f"부스 {len(cleaned)}개 일괄 등록", org_id=ev["org_id"],
              detail={"names": [c["name"] for c, _ in cleaned]})
    return jsonify(created=len(cleaned)), 201


@bp.get("/booths/<int:booth_id>")
@login_required
def get_booth(booth_id: int):
    return jsonify(booth=booth_json(require_booth(booth_id, "manage")))


@bp.patch("/booths/<int:booth_id>")
@login_required
def patch_booth(booth_id: int):
    body = json_body()
    moving = "event_id" in body or "partner_id" in body
    booth = require_booth(booth_id, "own" if moving else "manage")
    p = current_principal()
    clean = validate_settings({k: body[k] for k in SETTING_FIELDS if k in body}, base=booth)

    new_event, new_partner = booth["event_id"], booth["partner_id"]
    errors: dict[str, str] = {}
    if "event_id" in body:
        target = None
        if _is_id(body["event_id"]):
            target = get_db().execute("SELECT id, org_id FROM events WHERE id = ?",
                                      (body["event_id"],)).fetchone()
        if target is None or target["org_id"] != booth["org_id"]:
            errors["event_id"] = "같은 기관의 행사여야 합니다."
        elif target["id"] != booth["event_id"]:
            new_event, new_partner = target["id"], None  # 행사를 옮기면 운영 단체는 미배정
    if "partner_id" in body and not errors:
        try:
            new_partner = _check_partner(body["partner_id"], new_event)
        except ValidationError as e:
            errors.update(e.fields)
    if errors:
        raise ValidationError(errors)

    with tx() as conn:
        apply_settings(p, booth, clean)
        if (new_event, new_partner) != (booth["event_id"], booth["partner_id"]):
            conn.execute("UPDATE booths SET event_id = ?, partner_id = ? WHERE id = ?",
                         (new_event, new_partner, booth_id))
            audit(p, "booth.move", f"booth:{booth_id}", f"{booth['name']} 배정 변경",
                  org_id=booth["org_id"],
                  detail={"event_id": {"from": booth["event_id"], "to": new_event},
                          "partner_id": {"from": booth["partner_id"], "to": new_partner}})
    return jsonify(booth=booth_json(load_booth(booth_id)))


@bp.delete("/booths/<int:booth_id>")
@login_required
def delete_booth(booth_id: int):
    booth = require_booth(booth_id, "own")
    with tx() as conn:
        n = conn.execute("SELECT COUNT(*) FROM reservations WHERE booth_id = ?", (booth_id,)).fetchone()[0]
        conn.execute("DELETE FROM booths WHERE id = ?", (booth_id,))
        audit(current_principal(), "booth.delete", f"booth:{booth_id}", f"부스 삭제: {booth['name']}",
              org_id=booth["org_id"], detail={"reservation_count": n})
    return "", 204


@bp.post("/booths/<int:booth_id>/toggle")
@login_required
def toggle_booth(booth_id: int):
    booth = require_booth(booth_id, "manage")
    return jsonify(is_active=toggle(current_principal(), booth))


@bp.post("/booths/batch-status")
@login_required
def batch_status():
    body = json_body()
    ids, active = body.get("booth_ids"), body.get("is_active")
    if not isinstance(ids, list) or not ids or not all(_is_id(i) for i in ids):
        raise ValidationError({"booth_ids": "부스 ID 목록이 필요합니다."})
    if not isinstance(active, bool):
        raise ValidationError({"is_active": "true/false 여야 합니다."})
    p = current_principal()
    booths = [require_booth(i, "own") for i in dict.fromkeys(ids)]
    with tx() as conn:
        conn.executemany("UPDATE booths SET is_active = ? WHERE id = ?",
                         [(int(active), b["id"]) for b in booths])
        # 감사 1건. 기관이 여럿(슈퍼관리자)일 수 있어 첫 부스의 기관을 대표로 쓴다.
        audit(p, "booth.batch_status", None,
              f"부스 {len(booths)}개 {'운영 시작' if active else '운영 중지'}",
              org_id=booths[0]["org_id"],
              detail={"booth_ids": [b["id"] for b in booths], "is_active": active})
    return jsonify(updated=len(booths))


@bp.post("/booths/<int:booth_id>/rotate-token")
@login_required
def rotate_token(booth_id: int):
    booth = require_booth(booth_id, "own")
    token = link_token()
    with tx() as conn:
        conn.execute("UPDATE booths SET manage_token = ? WHERE id = ?", (token, booth_id))
        audit(current_principal(), "booth.rotate_token", f"booth:{booth_id}",
              f"부스 링크 재발급: {booth['name']}", org_id=booth["org_id"])
    return jsonify(manage_token=token)


@bp.get("/booths/<int:booth_id>/reservations")
@login_required
def booth_reservations(booth_id: int):
    require_booth(booth_id, "manage")
    return jsonify(reservations=list_reservations(booth_id))


def _reservation_booth(rid: int) -> sqlite3.Row:
    row = get_db().execute("SELECT booth_id FROM reservations WHERE id = ?", (rid,)).fetchone()
    if row is None:
        not_found()
    return require_booth(row["booth_id"], "manage")


@bp.patch("/reservations/<int:rid>")
@login_required
def patch_reservation(rid: int):
    body = json_body()
    booth = _reservation_booth(rid)
    return jsonify(set_status(current_principal(), booth, rid, body.get("status")))


@bp.delete("/reservations/<int:rid>")
@login_required
def remove_reservation(rid: int):
    delete_reservation(current_principal(), _reservation_booth(rid), rid)
    return "", 204


@bp.delete("/booths/<int:booth_id>/reservations")
@login_required
def clear_booth_reservations(booth_id: int):
    booth = require_booth(booth_id, "manage")
    return jsonify(deleted=clear_reservations(current_principal(), booth))


@bp.post("/stats")
@login_required
def stats_route():
    body = json_body()
    ids = parse_booth_ids(body)
    mode = parse_stats_mode(body)
    for i in ids:
        require_booth(i, "manage")
    return jsonify(stats(ids, mode))


@bp.post("/export")
@login_required
def export_route():
    ids = parse_booth_ids(json_body())
    for i in ids:
        require_booth(i, "manage")
    return jsonify(export(current_principal(), ids))


def int_param(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValidationError({name: "정수여야 합니다."}) from None


def audit_query(org_id: int | None) -> dict:
    return list_audit(org_id=org_id, before=int_param("before"), limit=int_param("limit", 50),
                      action=request.args.get("action") or None)


def _booths_of(event_id: int, partner_id: int | None = None) -> list[dict]:
    sql, args = BOOTH_SQL + " WHERE b.event_id = ?", [event_id]
    if partner_id is not None:
        sql += " AND b.partner_id = ?"
        args.append(partner_id)
    rows = get_db().execute(sql + " ORDER BY b.created_at DESC, b.id DESC", args).fetchall()
    return [booth_json(r) for r in rows]


@bp.get("/orgs/<int:org_id>/overview")
@login_required
def overview(org_id: int):
    org = require_org(org_id)
    db = get_db()
    owned, linked = [], []
    for e in db.execute("SELECT id, name FROM events WHERE org_id = ? ORDER BY created_at DESC, id DESC",
                        (org_id,)).fetchall():
        owned.append({"event": {"id": e["id"], "name": e["name"]}, "booths": _booths_of(e["id"])})
    for p in db.execute(
            "SELECT p.id, p.name, e.id AS event_id, e.name AS event_name, o.id AS host_id, o.name AS host_name "
            "FROM partners p JOIN events e ON e.id = p.event_id JOIN organizations o ON o.id = e.org_id "
            "WHERE p.linked_org_id = ? AND o.is_active = 1 ORDER BY e.created_at DESC, p.id DESC",
            (org_id,)).fetchall():
        linked.append({"partner": {"id": p["id"], "name": p["name"]},
                       "event": {"id": p["event_id"], "name": p["event_name"]},
                       "host": {"id": p["host_id"], "name": p["host_name"]},
                       "booths": _booths_of(p["event_id"], p["id"])})
    booths = [b for g in (*owned, *linked) for b in g["booths"]]
    ids = [b["id"] for b in booths]
    today = timeutil.kst_date(timeutil.now())
    n_today = 0
    if ids:
        marks = ",".join("?" for _ in ids)
        n_today = sum(1 for r in db.execute(
            f"SELECT created_at FROM reservations WHERE booth_id IN ({marks}) AND status != 'noshow'", ids)
            if timeutil.kst_date(r["created_at"]) == today)
    return jsonify(
        org={"id": org["id"], "name": org["name"], "slug": org["slug"]}, owned=owned, linked=linked,
        totals={"today": n_today, "total": sum(b["count"] for b in booths),
                "active_booths": sum(1 for b in booths if b["is_active"]), "booths": len(booths)})


@bp.get("/orgs/<int:org_id>/audit")
@login_required
def org_audit(org_id: int):
    require_org(org_id)
    return jsonify(audit_query(org_id))
