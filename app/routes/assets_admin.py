"""기관·행사 로고 업로드와 삭제."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import assets
from ..access import require_event, require_org
from ..audit import audit
from ..auth import login_required
from ..db import tx
from ..errors import ValidationError
from ..principal import current_principal

bp = Blueprint("assets_admin", __name__, url_prefix="/api/admin")

_NO_FILE = "이미지 파일을 선택해 주세요."


def _read_upload() -> bytes:
    f = request.files.get("file") if request.mimetype == "multipart/form-data" else None
    data = f.read(assets.MAX_BYTES + 1) if f else b""
    if not data:
        raise ValidationError({"file": _NO_FILE})
    return data


def _set_logo(table: str, row_id: int, org_id: int, data: bytes,
              action: str, label: str):
    name = assets.store(data)
    with tx() as conn:
        old = conn.execute(f"SELECT logo FROM {table} WHERE id = ?", (row_id,)).fetchone()["logo"]
        conn.execute(f"UPDATE {table} SET logo = ? WHERE id = ?", (name, row_id))
        audit(current_principal(), action, f"{label}:{row_id}", "로고 설정",
              org_id=org_id, detail={"action": "set"})
    if old != name:
        assets.release(old)
    return jsonify(logo_url=assets.url(name))


def _delete_logo(table: str, row_id: int, org_id: int, action: str, label: str):
    with tx() as conn:
        old = conn.execute(f"SELECT logo FROM {table} WHERE id = ?", (row_id,)).fetchone()["logo"]
        if old is None:
            return "", 204
        conn.execute(f"UPDATE {table} SET logo = NULL WHERE id = ?", (row_id,))
        audit(current_principal(), action, f"{label}:{row_id}", "로고 삭제",
              org_id=org_id, detail={"action": "delete"})
    assets.release(old)
    return "", 204


@bp.put("/orgs/<int:org_id>/logo")
@login_required
def put_org_logo(org_id: int):
    require_org(org_id)
    return _set_logo("organizations", org_id, org_id, _read_upload(), "org.logo", "org")


@bp.delete("/orgs/<int:org_id>/logo")
@login_required
def delete_org_logo(org_id: int):
    require_org(org_id)
    return _delete_logo("organizations", org_id, org_id, "org.logo", "org")


@bp.put("/events/<int:event_id>/logo")
@login_required
def put_event_logo(event_id: int):
    ev = require_event(event_id)
    return _set_logo("events", event_id, ev["org_id"], _read_upload(), "event.logo", "event")


@bp.delete("/events/<int:event_id>/logo")
@login_required
def delete_event_logo(event_id: int):
    ev = require_event(event_id)
    return _delete_logo("events", event_id, ev["org_id"], "event.logo", "event")
