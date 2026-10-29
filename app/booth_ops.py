"""부스 설정 검증·직렬화·변경. 계정 라우트와 링크 라우트가 함께 쓴다(Principal 을 인자로 받는다)."""

from __future__ import annotations

import sqlite3
from typing import Mapping

from .access import load_booth
from .audit import audit
from .capacity import availability, occupancy
from .db import get_db, tx
from .errors import ValidationError
from .principal import Principal

SETTING_FIELDS = ("name", "mode", "use_waitlist", "total_limit", "start_hour", "end_hour",
                  "slots_per_hour", "limit_per_slot")

DEFAULTS = {"name": "", "mode": "time", "use_waitlist": 0, "total_limit": 0, "start_hour": 11,
            "end_hour": 16, "slots_per_hour": 3, "limit_per_slot": 0}

_LABELS = {"name": "이름", "mode": "운영 방식", "use_waitlist": "대기 신청", "total_limit": "정원",
           "start_hour": "시작 시각", "end_hour": "종료 시각", "slots_per_hour": "시간당 타임 수",
           "limit_per_slot": "타임당 정원"}
_INT_FIELDS = ("total_limit", "start_hour", "end_hour", "slots_per_hour", "limit_per_slot")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_settings(body: dict, base: Mapping | None = None) -> dict:
    """주어진 키만 검증·정규화해 돌려준다. 시간 범위는 base 와 합친 결과로 검사한다."""
    out: dict = {}
    errors: dict[str, str] = {}
    if "name" in body:
        v = body["name"]
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 100:
            errors["name"] = "1~100자로 입력하세요."
        else:
            out["name"] = v.strip()
    if "mode" in body:
        if body["mode"] not in ("time", "fcfs"):
            errors["mode"] = "time 또는 fcfs 여야 합니다."
        else:
            out["mode"] = body["mode"]
    if "use_waitlist" in body:
        v = body["use_waitlist"]
        if isinstance(v, bool) or (_is_int(v) and v in (0, 1)):
            out["use_waitlist"] = int(v)
        else:
            errors["use_waitlist"] = "true/false 여야 합니다."
    for f in _INT_FIELDS:
        if f in body:
            v = body[f]
            if not _is_int(v) or v < 0:
                errors[f] = "0 이상의 정수여야 합니다."
            else:
                out[f] = v
    merged = {**(base or {}), **out}
    s, e = merged.get("start_hour"), merged.get("end_hour")
    if "start_hour" not in errors and "end_hour" not in errors and s is not None and e is not None:
        if not 0 <= s <= e <= 23:
            errors["end_hour"] = "시작 시각 이상 23 이하여야 합니다."
    if "slots_per_hour" in out and not 1 <= out["slots_per_hour"] <= 6:
        errors["slots_per_hour"] = "1~6 사이여야 합니다."
    if errors:
        raise ValidationError(errors)
    return out


def booth_json(booth: sqlite3.Row, occ: dict | None = None) -> dict:
    db = get_db()
    if occ is None:
        occ = occupancy(db, booth["id"])
    count = db.execute("SELECT COUNT(*) FROM reservations WHERE booth_id = ? AND status != 'noshow'",
                       (booth["id"],)).fetchone()[0]
    return {
        "id": booth["id"], "event_id": booth["event_id"], "event_name": booth["event_name"],
        "org_id": booth["org_id"], "partner_id": booth["partner_id"],
        "partner_name": booth["partner_name"], "name": booth["name"], "mode": booth["mode"],
        "use_waitlist": bool(booth["use_waitlist"]), "total_limit": booth["total_limit"],
        "start_hour": booth["start_hour"], "end_hour": booth["end_hour"],
        "slots_per_hour": booth["slots_per_hour"], "limit_per_slot": booth["limit_per_slot"],
        "is_active": bool(booth["is_active"]), "manage_token": booth["manage_token"],
        "count": count, "availability": availability(booth, occ),
    }


def _show(field: str, v) -> str:
    if field == "use_waitlist":
        return "사용" if v else "미사용"
    if field == "mode":
        return "선착순" if v == "fcfs" else "시간대별"
    return str(v)


def apply_settings(p: Principal, booth: sqlite3.Row, clean: dict) -> None:
    """트랜잭션 안에서 호출. 바뀐 항목이 있을 때만 쓰고 감사 기록."""
    changed = {k: v for k, v in clean.items() if booth[k] != v}
    if not changed:
        return
    sets = ", ".join(f"{k} = ?" for k in changed)
    get_db().execute(f"UPDATE booths SET {sets} WHERE id = ?", (*changed.values(), booth["id"]))
    parts = ", ".join(f"{_LABELS[k]} {_show(k, booth[k])} → {_show(k, v)}" for k, v in changed.items())
    audit(p, "booth.update", f"booth:{booth['id']}", f"{booth['name']} 설정 변경: {parts}",
          org_id=booth["org_id"],
          detail={k: {"from": booth[k], "to": v} for k, v in changed.items()})


def update_settings(p: Principal, booth: sqlite3.Row, body: dict) -> sqlite3.Row:
    clean = validate_settings(body, base=booth)
    with tx():
        apply_settings(p, booth, clean)
    return load_booth(booth["id"])


def toggle(p: Principal, booth: sqlite3.Row) -> bool:
    new = 0 if booth["is_active"] else 1
    with tx() as conn:
        conn.execute("UPDATE booths SET is_active = ? WHERE id = ?", (new, booth["id"]))
        audit(p, "booth.toggle", f"booth:{booth['id']}",
              f"{booth['name']} {'운영 시작' if new else '운영 중지'}", org_id=booth["org_id"],
              detail={"is_active": bool(new)})
    return bool(new)
