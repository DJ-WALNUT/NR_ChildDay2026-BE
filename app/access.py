"""접근 범위 검사. 범위 밖 자원은 존재를 숨기기 위해 404."""

from __future__ import annotations

import sqlite3
from typing import Literal, NoReturn

from flask import request

from .auth import current_user
from .db import get_db
from .errors import ApiError, forbidden, not_found
from .principal import Principal, current_principal, set_principal
from .ratelimit import guard


def require_org(org_id: int) -> sqlite3.Row:
    u = current_user()
    if u is None or (not u.is_superadmin and u.org_id != org_id):
        not_found()
    row = get_db().execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if row is None:
        not_found()
    return row


def require_user_target(user_id: int) -> sqlite3.Row:
    u = current_user()
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if u is None or row is None:
        not_found()
    if not u.is_superadmin and (row["is_superadmin"] or row["org_id"] != u.org_id):
        not_found()
    return row


BOOTH_SQL = """
SELECT b.*, e.name AS event_name, o.id AS org_id, o.name AS org_name, o.slug AS org_slug,
       o.is_active AS org_active, p.name AS partner_name, p.linked_org_id AS partner_linked_org_id
FROM booths b
JOIN events e ON e.id = b.event_id
JOIN organizations o ON o.id = e.org_id
LEFT JOIN partners p ON p.id = b.partner_id
"""

PARTNER_SQL = """
SELECT p.*, e.name AS event_name, o.id AS org_id, o.name AS org_name, o.is_active AS org_active
FROM partners p
JOIN events e ON e.id = p.event_id
JOIN organizations o ON o.id = e.org_id
"""


def load_booth(booth_id: int) -> sqlite3.Row | None:
    return get_db().execute(BOOTH_SQL + " WHERE b.id = ?", (booth_id,)).fetchone()


def booth_access(p: Principal, booth: sqlite3.Row) -> Literal["own", "manage"] | None:
    if not booth["org_active"]:
        return "own" if p.is_superadmin else None
    if p.kind == "user":
        if p.is_superadmin or (p.org_id is not None and p.org_id == booth["org_id"]):
            return "own"
        if p.org_id is not None and booth["partner_linked_org_id"] == p.org_id:
            return "manage"
    elif p.kind == "booth_link":
        if p.booth_id == booth["id"]:
            return "manage"
    elif p.kind == "partner_link":
        if p.partner_id is not None and p.partner_id == booth["partner_id"]:
            return "manage"
    return None


def require_booth(booth_id: int, need: Literal["manage", "own"] = "manage",
                  p: Principal | None = None) -> sqlite3.Row:
    p = p or current_principal()
    booth = load_booth(booth_id)
    level = booth_access(p, booth) if p is not None and booth is not None else None
    if level is None:
        not_found()
    if need == "own" and level != "own":
        forbidden()
    return booth


def require_event(event_id: int) -> sqlite3.Row:
    u = current_user()
    row = get_db().execute(
        "SELECT e.*, o.name AS org_name, o.is_active AS org_active FROM events e"
        " JOIN organizations o ON o.id = e.org_id WHERE e.id = ?", (event_id,)).fetchone()
    if u is None or row is None:
        not_found()
    if not u.is_superadmin and (u.org_id != row["org_id"] or not row["org_active"]):
        not_found()
    return row


def _link_miss() -> NoReturn:
    guard(f"link-miss:{request.remote_addr}", 30, 900)
    raise ApiError(404, "not_found")


def resolve_booth_link(token: str) -> tuple[Principal, sqlite3.Row]:
    booth = get_db().execute(BOOTH_SQL + " WHERE b.manage_token = ?", (token,)).fetchone()
    if booth is None or not booth["org_active"]:
        _link_miss()
    p = Principal(kind="booth_link", label=f"부스 링크: {booth['name']}", booth_id=booth["id"])
    set_principal(p)
    return p, booth


def resolve_partner_link(token: str) -> tuple[Principal, sqlite3.Row]:
    partner = get_db().execute(PARTNER_SQL + " WHERE p.access_token = ?", (token,)).fetchone()
    if partner is None or not partner["org_active"]:
        _link_miss()
    p = Principal(kind="partner_link", label=f"실적 링크: {partner['name']}", partner_id=partner["id"])
    set_principal(p)
    return p, partner
