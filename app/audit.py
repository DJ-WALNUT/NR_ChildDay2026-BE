"""감사 로그. 호출자의 트랜잭션 안에서 부르면 변경과 함께 커밋된다."""

from __future__ import annotations

import json

from .db import get_db
from .principal import Principal
from .timeutil import now


def audit(p: Principal | None, action: str, target: str | None, summary: str, *,
          org_id: int | None, detail: dict | None = None) -> None:
    actor, label = (p.kind, p.label) if p else ("system", "시스템")
    get_db().execute(
        "INSERT INTO audit_log (org_id, actor_org_id, user_id, actor, actor_label, action,"
        " target, summary, detail, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (org_id, p.org_id if p else None, p.user_id if p else None, actor, label, action,
         target, summary,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None, now()),
    )


def list_audit(*, org_id: int | None, before: int | None, limit: int, action: str | None) -> dict:
    limit = max(1, min(200, limit))
    where, args = [], []
    if org_id is not None:
        where.append("(org_id = ? OR actor_org_id = ?)")
        args += [org_id, org_id]
    if before is not None:
        where.append("id < ?")
        args.append(before)
    if action:
        where.append("substr(action, 1, ?) = ?")
        args += [len(action), action]
    sql = "SELECT * FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    rows = get_db().execute(sql, (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        items.append(d)
    return {"items": items, "next_before": items[-1]["id"] if more else None}
