"""링크 API (부스 링크, 실적 링크). 로그인·CSRF 없이 토큰만으로 접근한다."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..access import require_booth, resolve_booth_link, resolve_partner_link
from ..booth_ops import (SETTING_FIELDS, booth_json, clear_reservations, delete_reservation, export,
                         list_reservations, parse_booth_ids, set_status, stats, toggle, update_settings)
from ..db import get_db
from ..errors import ValidationError, forbidden
from .common import json_body

bp = Blueprint("links", __name__, url_prefix="/api/link")


# ---- 부스/실적 링크가 공유하는 부스 단위 동작 (principal, booth) ----

def _get(p, booth):
    return jsonify(booth=booth_json(booth))


def _patch(p, booth):
    body = json_body()
    if any(k not in SETTING_FIELDS for k in body):
        forbidden()
    return jsonify(booth=booth_json(update_settings(p, booth, body)))


def _toggle(p, booth):
    return jsonify(is_active=toggle(p, booth))


def _list_res(p, booth):
    return jsonify(reservations=list_reservations(booth["id"]))


def _patch_res(p, booth, rid):
    status = json_body().get("status")
    if not isinstance(status, str):
        raise ValidationError({"status": "올바르지 않은 상태입니다."})
    return jsonify(set_status(p, booth, rid, status))


def _delete_res(p, booth, rid):
    delete_reservation(p, booth, rid)
    return "", 204


def _clear(p, booth):
    return jsonify(deleted=clear_reservations(p, booth))


def _stats_mode(body) -> str:
    mode = body.get("mode", "simple")
    if mode not in ("simple", "strict"):
        raise ValidationError({"mode": "simple 또는 strict 여야 합니다."})
    return mode


# ---- 부스 링크 ----

def _booth(token):
    p, row = resolve_booth_link(token)
    return p, require_booth(row["id"], "manage", p)


@bp.get("/booth/<token>")
def booth_get(token):
    p, booth = _booth(token)
    return jsonify(booth=booth_json(booth),
                   org={"name": booth["org_name"], "slug": booth["org_slug"]},
                   reservations=list_reservations(booth["id"]),
                   stats=stats([booth["id"]], "simple"))


@bp.patch("/booth/<token>")
def booth_patch(token):
    return _patch(*_booth(token))


@bp.post("/booth/<token>/toggle")
def booth_toggle(token):
    return _toggle(*_booth(token))


@bp.patch("/booth/<token>/reservations/<int:rid>")
def booth_res_patch(token, rid):
    return _patch_res(*_booth(token), rid)


@bp.delete("/booth/<token>/reservations/<int:rid>")
def booth_res_delete(token, rid):
    return _delete_res(*_booth(token), rid)


@bp.delete("/booth/<token>/reservations")
def booth_res_clear(token):
    return _clear(*_booth(token))


@bp.get("/booth/<token>/export")
def booth_export(token):
    p, booth = _booth(token)
    return jsonify(export(p, [booth["id"]]))


# ---- 실적 링크 ----

def _partner_booth(token, booth_id):
    p, _ = resolve_partner_link(token)
    return p, require_booth(booth_id, "manage", p)


def _scoped_ids(p, body) -> list[int]:
    if "booth_ids" in body:
        ids = parse_booth_ids(body)
    else:
        ids = [r["id"] for r in get_db().execute(
            "SELECT id FROM booths WHERE partner_id = ? ORDER BY name, id", (p.partner_id,))]
    for i in ids:
        require_booth(i, "manage", p)
    return ids


@bp.get("/partner/<token>")
def partner_get(token):
    p, partner = resolve_partner_link(token)
    db = get_db()
    slug = db.execute("SELECT slug FROM organizations WHERE id = ?", (partner["org_id"],)).fetchone()[0]
    rows = db.execute("SELECT id FROM booths WHERE partner_id = ? ORDER BY name, id",
                      (partner["id"],)).fetchall()
    return jsonify(partner={"id": partner["id"], "name": partner["name"]},
                   event={"id": partner["event_id"], "name": partner["event_name"]},
                   org={"name": partner["org_name"], "slug": slug},
                   booths=[booth_json(require_booth(r["id"], "manage", p)) for r in rows])


@bp.post("/partner/<token>/stats")
def partner_stats(token):
    p, _ = resolve_partner_link(token)
    body = json_body()
    mode = _stats_mode(body)
    return jsonify(stats(_scoped_ids(p, body), mode))


@bp.post("/partner/<token>/export")
def partner_export(token):
    p, _ = resolve_partner_link(token)
    ids = _scoped_ids(p, json_body())
    return jsonify(export(p, ids) if ids else {"booths": []})  # 내보낼 것이 없으면 감사 기록도 없다


@bp.get("/partner/<token>/booths/<int:booth_id>")
def partner_booth_get(token, booth_id):
    return _get(*_partner_booth(token, booth_id))


@bp.patch("/partner/<token>/booths/<int:booth_id>")
def partner_booth_patch(token, booth_id):
    return _patch(*_partner_booth(token, booth_id))


@bp.post("/partner/<token>/booths/<int:booth_id>/toggle")
def partner_booth_toggle(token, booth_id):
    return _toggle(*_partner_booth(token, booth_id))


@bp.get("/partner/<token>/booths/<int:booth_id>/reservations")
def partner_res_list(token, booth_id):
    return _list_res(*_partner_booth(token, booth_id))


@bp.patch("/partner/<token>/booths/<int:booth_id>/reservations/<int:rid>")
def partner_res_patch(token, booth_id, rid):
    return _patch_res(*_partner_booth(token, booth_id), rid)


@bp.delete("/partner/<token>/booths/<int:booth_id>/reservations/<int:rid>")
def partner_res_delete(token, booth_id, rid):
    return _delete_res(*_partner_booth(token, booth_id), rid)


@bp.delete("/partner/<token>/booths/<int:booth_id>/reservations")
def partner_res_clear(token, booth_id):
    return _clear(*_partner_booth(token, booth_id))
