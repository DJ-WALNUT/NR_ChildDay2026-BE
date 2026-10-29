"""접근 범위 검사. 범위 밖 자원은 존재를 숨기기 위해 404."""

from __future__ import annotations

import sqlite3

from .auth import current_user
from .db import get_db
from .errors import not_found


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
