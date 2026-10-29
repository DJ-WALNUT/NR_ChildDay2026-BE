"""행사와 운영 단체(partner) 관리."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify

from ..access import require_event, require_org
from ..audit import audit
from ..auth import login_required
from ..db import get_db, tx
from ..errors import ValidationError, not_found
from ..principal import current_principal
from ..security import link_token
from ..timeutil import now
from .common import json_body

bp = Blueprint("admin_events", __name__, url_prefix="/api/admin")

_EVENT_SQL = (
    "SELECT e.id, e.name, e.created_at,"
    " (SELECT COUNT(*) FROM booths b WHERE b.event_id = e.id) AS booth_count,"
    " (SELECT COUNT(*) FROM partners p WHERE p.event_id = e.id) AS partner_count"
    " FROM events e")
_PARTNER_SQL = (
    "SELECT p.id, p.name, p.access_token, p.linked_org_id, lo.name AS linked_org_name,"
    " (SELECT COUNT(*) FROM booths b WHERE b.partner_id = p.id) AS booth_count"
    " FROM partners p LEFT JOIN organizations lo ON lo.id = p.linked_org_id")


def _event_out(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "created_at": r["created_at"],
            "booth_count": r["booth_count"], "partner_count": r["partner_count"]}


def _partner_out(r: sqlite3.Row) -> dict:
    linked = ({"id": r["linked_org_id"], "name": r["linked_org_name"]}
              if r["linked_org_id"] is not None else None)
    return {"id": r["id"], "name": r["name"], "linked_org": linked,
            "access_token": r["access_token"], "booth_count": r["booth_count"]}


def _load_event(event_id: int) -> dict:
    return _event_out(get_db().execute(_EVENT_SQL + " WHERE e.id = ?", (event_id,)).fetchone())


def _load_partner(partner_id: int) -> dict:
    return _partner_out(get_db().execute(_PARTNER_SQL + " WHERE p.id = ?", (partner_id,)).fetchone())


def _name(v, max_len: int = 100) -> str:
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= max_len:
        raise ValidationError({"name": f"1~{max_len}자로 입력하세요."})
    return v.strip()


def _linked_org(v, owner_org_id: int) -> int | None:
    if v is None:
        return None
    bad = ValidationError({"linked_org_id": "존재하는 활성 기관이어야 합니다."})
    if isinstance(v, bool) or not isinstance(v, int) or v == owner_org_id:
        raise bad
    row = get_db().execute("SELECT is_active FROM organizations WHERE id = ?", (v,)).fetchone()
    if row is None or not row["is_active"]:
        raise bad
    return v


def _partner_and_event(partner_id: int) -> tuple[sqlite3.Row, sqlite3.Row]:
    partner = get_db().execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
    if partner is None:
        not_found()
    return partner, require_event(partner["event_id"])


@bp.get("/orgs/<int:org_id>/events")
@login_required
def list_events(org_id: int):
    require_org(org_id)
    rows = get_db().execute(_EVENT_SQL + " WHERE e.org_id = ? ORDER BY e.created_at DESC, e.id DESC",
                            (org_id,)).fetchall()
    return jsonify(events=[_event_out(r) for r in rows])


@bp.post("/orgs/<int:org_id>/events")
@login_required
def create_event(org_id: int):
    require_org(org_id)
    name = _name(json_body().get("name"))
    with tx() as conn:
        cur = conn.execute("INSERT INTO events (org_id, name, created_at) VALUES (?,?,?)",
                           (org_id, name, now()))
        audit(current_principal(), "event.create", f"event:{cur.lastrowid}",
              f"행사 생성: {name}", org_id=org_id)
    return jsonify(event=_load_event(cur.lastrowid)), 201


@bp.patch("/events/<int:event_id>")
@login_required
def update_event(event_id: int):
    ev = require_event(event_id)
    name = _name(json_body().get("name"))
    with tx() as conn:
        conn.execute("UPDATE events SET name = ? WHERE id = ?", (name, event_id))
        audit(current_principal(), "event.update", f"event:{event_id}",
              f"행사 이름 변경: {ev['name']} → {name}", org_id=ev["org_id"],
              detail={"name": {"from": ev["name"], "to": name}})
    return jsonify(event=_load_event(event_id))


@bp.delete("/events/<int:event_id>")
@login_required
def delete_event(event_id: int):
    ev = require_event(event_id)
    with tx() as conn:
        booths = conn.execute("SELECT COUNT(*) FROM booths WHERE event_id = ?", (event_id,)).fetchone()[0]
        resv = conn.execute(
            "SELECT COUNT(*) FROM reservations r JOIN booths b ON b.id = r.booth_id"
            " WHERE b.event_id = ?", (event_id,)).fetchone()[0]
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        audit(current_principal(), "event.delete", f"event:{event_id}",
              f"행사 삭제: {ev['name']}", org_id=ev["org_id"],
              detail={"booth_count": booths, "reservation_count": resv})
    return "", 204


@bp.get("/events/<int:event_id>/partners")
@login_required
def list_partners(event_id: int):
    require_event(event_id)
    rows = get_db().execute(_PARTNER_SQL + " WHERE p.event_id = ? ORDER BY p.id", (event_id,)).fetchall()
    return jsonify(partners=[_partner_out(r) for r in rows])


@bp.post("/events/<int:event_id>/partners")
@login_required
def create_partner(event_id: int):
    ev = require_event(event_id)
    body = json_body()
    fields: dict[str, str] = {}
    name = linked = None
    try:
        name = _name(body.get("name"))
    except ValidationError as e:
        fields.update(e.fields)
    try:
        linked = _linked_org(body.get("linked_org_id"), ev["org_id"])
    except ValidationError as e:
        fields.update(e.fields)
    if fields:
        raise ValidationError(fields)
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO partners (event_id, name, linked_org_id, access_token, created_at)"
            " VALUES (?,?,?,?,?)", (event_id, name, linked, link_token(), now()))
        audit(current_principal(), "partner.create", f"partner:{cur.lastrowid}",
              f"운영 단체 생성: {name}", org_id=ev["org_id"], detail={"linked_org_id": linked})
    return jsonify(partner=_load_partner(cur.lastrowid)), 201


@bp.patch("/partners/<int:partner_id>")
@login_required
def update_partner(partner_id: int):
    partner, ev = _partner_and_event(partner_id)
    body = json_body()
    fields: dict[str, str] = {}
    name, linked = partner["name"], partner["linked_org_id"]
    if "name" in body:
        try:
            name = _name(body["name"])
        except ValidationError as e:
            fields.update(e.fields)
    if "linked_org_id" in body:
        try:
            linked = _linked_org(body["linked_org_id"], ev["org_id"])
        except ValidationError as e:
            fields.update(e.fields)
    if fields:
        raise ValidationError(fields)
    with tx() as conn:
        conn.execute("UPDATE partners SET name = ?, linked_org_id = ? WHERE id = ?",
                     (name, linked, partner_id))
        audit(current_principal(), "partner.update", f"partner:{partner_id}",
              f"운영 단체 수정: {name}", org_id=ev["org_id"],
              detail={"name": {"from": partner["name"], "to": name},
                      "linked_org_id": {"from": partner["linked_org_id"], "to": linked}})
    return jsonify(partner=_load_partner(partner_id))


@bp.delete("/partners/<int:partner_id>")
@login_required
def delete_partner(partner_id: int):
    partner, ev = _partner_and_event(partner_id)
    with tx() as conn:
        conn.execute("DELETE FROM partners WHERE id = ?", (partner_id,))
        audit(current_principal(), "partner.delete", f"partner:{partner_id}",
              f"운영 단체 삭제: {partner['name']}", org_id=ev["org_id"])
    return "", 204


@bp.post("/partners/<int:partner_id>/rotate-token")
@login_required
def rotate_partner_token(partner_id: int):
    partner, ev = _partner_and_event(partner_id)
    token = link_token()
    with tx() as conn:
        conn.execute("UPDATE partners SET access_token = ? WHERE id = ?", (token, partner_id))
        audit(current_principal(), "partner.rotate_token", f"partner:{partner_id}",
              f"운영 단체 링크 재발급: {partner['name']}", org_id=ev["org_id"])
    return jsonify(access_token=token)


@bp.get("/orgs/<int:org_id>/link-targets")
@login_required
def link_targets(org_id: int):
    require_org(org_id)
    rows = get_db().execute(
        "SELECT id, name FROM organizations WHERE is_active = 1 AND id != ? ORDER BY name, id",
        (org_id,)).fetchall()
    return jsonify(orgs=[{"id": r["id"], "name": r["name"]} for r in rows])
