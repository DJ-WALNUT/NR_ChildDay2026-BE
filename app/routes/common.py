"""라우트 공용 도구."""

from __future__ import annotations

from flask import request

from ..audit import list_audit
from ..errors import ValidationError


def json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


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
