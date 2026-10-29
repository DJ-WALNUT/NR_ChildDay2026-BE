"""방문객 API. 로그인·CSRF 없이 접근하며 신청자 확인은 서버가 이름+식별번호로 대조한다."""

from __future__ import annotations

import re

from flask import Blueprint, Response, jsonify, request

from .. import assets

from ..access import load_booth
from ..booth_ops import AGE_GROUPS, GENDERS
from ..capacity import availability, decide_status, occupancy
from ..db import get_db, tx
from ..errors import ApiError, ValidationError, conflict, not_found
from ..ratelimit import guard
from ..timeutil import now
from .common import json_body

bp = Blueprint("public", __name__, url_prefix="/api/public")


@bp.get("/assets/<name>")
def get_asset(name: str):
    p = assets.path_for(name)
    if p is None:
        raise ApiError(404, "not_found")
    try:
        data = p.read_bytes()  # 파일 핸들을 열어 두지 않는다(Windows 에서 삭제가 막힘)
    except OSError:
        raise ApiError(404, "not_found")
    resp = Response(data, mimetype=assets.CONTENT_TYPES[name.rsplit(".", 1)[1]])
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


def normalize_name(s) -> str:
    if not isinstance(s, str) or not 1 <= len(s.strip()) <= 50:
        raise ValidationError({"name": "이름은 1~50자로 입력해 주세요."})
    return s.strip()


def normalize_code(s) -> str:
    if isinstance(s, str) and any(c.isdigit() and c not in "0123456789" for c in s):
        raise ValidationError({"code": "식별번호는 숫자 4자리입니다."})
    digits = re.sub(r"[^0-9]", "", s) if isinstance(s, str) else ""
    if len(digits) != 4:
        raise ValidationError({"code": "식별번호는 숫자 4자리입니다."})
    return digits


def _identity(body: dict) -> tuple[str, str]:
    fields: dict[str, str] = {}
    out = {}
    for key, fn in (("name", normalize_name), ("code", normalize_code)):
        try:
            out[key] = fn(body.get(key))
        except ValidationError as e:
            fields.update(e.fields)
    if fields:
        raise ValidationError(fields)
    return out["name"], out["code"]


def _public_booth(booth_id: int):
    booth = load_booth(booth_id)
    if booth is None or not booth["org_active"]:
        not_found()
    return booth


def _find(db, booth_id: int, name: str, code: str):
    return db.execute("SELECT * FROM reservations WHERE booth_id=? AND name=? AND code=?",
                      (booth_id, name, code)).fetchone()


@bp.get("/orgs/<slug>")
def org_page(slug):
    db = get_db()
    org = db.execute("SELECT * FROM organizations WHERE slug=? AND is_active=1", (slug,)).fetchone()
    if org is None:
        not_found()
    events = []
    for ev in db.execute("SELECT * FROM events WHERE org_id=? ORDER BY created_at DESC, id DESC",
                         (org["id"],)).fetchall():
        booths = []
        for b in db.execute("SELECT * FROM booths WHERE event_id=? AND is_active=1 ORDER BY name, id",
                            (ev["id"],)).fetchall():
            a = availability(b, occupancy(db, b["id"]))
            booths.append({"id": b["id"], "name": b["name"], "mode": b["mode"], "is_closed": a["is_closed"]})
        if booths:
            events.append({"id": ev["id"], "name": ev["name"], "logo_url": assets.url(ev["logo"]),
                           "notice": ev["notice"], "booths": booths})
    return jsonify(org={"name": org["name"], "slug": org["slug"], "logo_url": assets.url(org["logo"])},
                   events=events)


@bp.get("/orgs/<slug>/booths/<int:booth_id>")
def booth_page(slug, booth_id):
    booth = _public_booth(booth_id)
    if booth["org_slug"] != slug:
        not_found()
    return jsonify(
        org={"name": booth["org_name"], "slug": booth["org_slug"],
             "logo_url": assets.url(booth["org_logo"])},
        event={"id": booth["event_id"], "name": booth["event_name"],
               "logo_url": assets.url(booth["event_logo"]), "notice": booth["event_notice"]},
        booth={"id": booth["id"], "name": booth["name"], "mode": booth["mode"],
               "use_waitlist": bool(booth["use_waitlist"]), "is_active": bool(booth["is_active"])},
        availability=availability(booth, occupancy(get_db(), booth_id)))


@bp.get("/booths/<int:booth_id>/home")
def booth_home(booth_id):
    return jsonify(slug=_public_booth(booth_id)["org_slug"])


@bp.post("/booths/<int:booth_id>/reservations")
def reserve(booth_id):
    guard(f"reserve:{request.remote_addr}", 30, 60)
    body = json_body()
    fields: dict[str, str] = {}
    name = code = ""
    try:
        name, code = _identity(body)
    except ValidationError as e:
        fields.update(e.fields)
    if body.get("gender") not in GENDERS:
        fields["gender"] = "성별을 선택해 주세요."
    if body.get("age_group") not in AGE_GROUPS:
        fields["age_group"] = "연령대를 선택해 주세요."
    slot = body.get("slot")
    if slot is not None and not isinstance(slot, str):
        fields["slot"] = "선택할 수 없는 시간입니다."
    if fields:
        raise ValidationError(fields)
    with tx() as db:
        booth = _public_booth(booth_id)
        if not booth["is_active"]:
            raise ApiError(409, "closed")
        if _find(db, booth_id, name, code) is not None:
            conflict("duplicate")
        slot, status = decide_status(booth, occupancy(db, booth_id), slot)
        cur = db.execute(
            "INSERT INTO reservations (booth_id, name, gender, age_group, code, slot, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (booth_id, name, body["gender"], body["age_group"], code, slot, status, now()))
        rid = cur.lastrowid
    return jsonify(id=rid, slot=slot, status=status), 201


@bp.post("/booths/<int:booth_id>/lookup")
def lookup(booth_id):
    guard(f"lookup:{request.remote_addr}", 20, 900)
    booth = _public_booth(booth_id)
    name, code = _identity(json_body())
    r = _find(get_db(), booth_id, name, code)
    if r is None:
        not_found()
    return jsonify(name=r["name"], slot=r["slot"], status=r["status"], booth_name=booth["name"])


@bp.post("/booths/<int:booth_id>/cancel")
def cancel(booth_id):
    guard(f"lookup:{request.remote_addr}", 20, 900)
    _public_booth(booth_id)
    name, code = _identity(json_body())
    with tx() as db:
        r = _find(db, booth_id, name, code)
        if r is None:
            not_found()
        if r["status"] not in ("normal", "waiting"):
            raise ApiError(409, "not_cancellable")
        db.execute("DELETE FROM reservations WHERE id=?", (r["id"],))
    return "", 204
