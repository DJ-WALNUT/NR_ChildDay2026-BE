"""부스 설정 검증·직렬화·변경. 계정 라우트와 링크 라우트가 함께 쓴다(Principal 을 인자로 받는다)."""

from __future__ import annotations

import re
import sqlite3
from typing import Literal, Mapping

from .access import load_booth
from .audit import audit
from .capacity import availability, occupancy
from .db import get_db, tx
from .errors import ValidationError, not_found
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
    with tx() as conn:
        conn.execute("UPDATE booths SET is_active = 1 - is_active WHERE id = ?", (booth["id"],))
        new = conn.execute("SELECT is_active FROM booths WHERE id = ?", (booth["id"],)).fetchone()[0]
        audit(p, "booth.toggle", f"booth:{booth['id']}",
              f"{booth['name']} {'운영 시작' if new else '운영 중지'}", org_id=booth["org_id"],
              detail={"is_active": bool(new)})
    return bool(new)


AGE_GROUPS = ("0~8세", "9~13세", "14~16세", "17~19세", "20~24세", "24세 이상")
GENDERS = ("남", "여")
STATUSES = ("normal", "waiting", "noshow", "completed")
MAX_BOOTHS = 500


def reservation_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "booth_id": r["booth_id"], "name": r["name"], "gender": r["gender"],
            "age_group": r["age_group"], "code": r["code"], "slot": r["slot"], "status": r["status"],
            "created_at": r["created_at"]}


def _slot_key(r: dict) -> tuple:
    m = re.match(r"(\d+)시", r["slot"])
    return (int(m.group(1)) if m else -1, r["slot"], r["id"])


def list_reservations(booth_id: int) -> list[dict]:
    rows = get_db().execute("SELECT * FROM reservations WHERE booth_id = ?", (booth_id,)).fetchall()
    return sorted((reservation_json(r) for r in rows), key=_slot_key)


def _own_reservation(booth: sqlite3.Row, rid: int) -> sqlite3.Row:
    r = get_db().execute("SELECT * FROM reservations WHERE id = ? AND booth_id = ?",
                         (rid, booth["id"])).fetchone()
    if r is None:
        not_found()
    return r


def set_status(p: Principal, booth: sqlite3.Row, rid: int, status: str) -> dict:
    if not isinstance(status, str) or status not in STATUSES:
        raise ValidationError({"status": "올바르지 않은 상태입니다."})
    r = _own_reservation(booth, rid)
    with tx() as conn:
        conn.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, rid))
        over = False
        if r["status"] == "waiting" and status == "normal":
            limit = booth["total_limit"] if booth["mode"] == "fcfs" else booth["limit_per_slot"]
            used = occupancy(conn, booth["id"]).get(r["slot"], 0)
            over = limit > 0 and used > limit
    out = {"reservation": reservation_json(get_db().execute(
        "SELECT * FROM reservations WHERE id = ?", (rid,)).fetchone())}
    if over:
        out["over_capacity"] = True
    return out


def delete_reservation(p: Principal, booth: sqlite3.Row, rid: int) -> None:
    r = _own_reservation(booth, rid)
    with tx() as conn:
        conn.execute("DELETE FROM reservations WHERE id = ?", (rid,))
        audit(p, "reservation.delete", f"booth:{booth['id']}",
              f"신청 삭제: {r['name']} ({booth['name']} {r['slot']})", org_id=booth["org_id"])


def clear_reservations(p: Principal, booth: sqlite3.Row) -> int:
    with tx() as conn:
        n = conn.execute("DELETE FROM reservations WHERE booth_id = ?", (booth["id"],)).rowcount
        audit(p, "reservation.clear", f"booth:{booth['id']}", f"{booth['name']} 신청 {n}건 초기화",
              org_id=booth["org_id"], detail={"deleted": n})
    return n


def parse_booth_ids(body) -> list[int]:
    ids = body.get("booth_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BOOTHS
            or not all(_is_int(i) for i in ids)):
        raise ValidationError({"booth_ids": f"부스 ID {MAX_BOOTHS}개 이하의 목록이 필요합니다."})
    return list(dict.fromkeys(ids))


def parse_stats_mode(body) -> Literal["simple", "strict"]:
    mode = body.get("mode", "simple")
    if mode not in ("simple", "strict"):
        raise ValidationError({"mode": "simple 또는 strict 여야 합니다."})
    return mode


def partner_booth_ids(partner_id: int) -> list[int]:
    rows = get_db().execute("SELECT id FROM booths WHERE partner_id = ? ORDER BY name, id",
                            (partner_id,)).fetchall()
    return [r["id"] for r in rows]


def stats(booth_ids: list[int], mode: Literal["simple", "strict"]) -> dict:
    marks = ",".join("?" for _ in booth_ids)
    rows = get_db().execute(
        f"SELECT name, gender, age_group FROM reservations "
        f"WHERE booth_id IN ({marks}) AND status != 'noshow'", booth_ids).fetchall()
    people = [tuple(r) for r in rows]
    if mode == "strict":
        people = list(set(people))
    table = {a: {"age_group": a, "male": 0, "female": 0, "total": 0} for a in AGE_GROUPS}
    for _name, gender, age in people:
        row = table.get(age)
        if row is None or gender not in GENDERS:
            continue
        row["male" if gender == "남" else "female"] += 1
        row["total"] += 1
    out = list(table.values())
    male, female = sum(r["male"] for r in out), sum(r["female"] for r in out)
    return {"rows": out, "male": male, "female": female, "total": male + female}


def export(p: Principal, booth_ids: list[int]) -> dict:
    booths, by_org = [], {}
    for bid in booth_ids:
        b = load_booth(bid)
        by_org.setdefault(b["org_id"], []).append(bid)
        rows = [{k: v for k, v in r.items() if k not in ("id", "booth_id")} for r in list_reservations(bid)]
        booths.append({"booth_id": bid, "event_name": b["event_name"], "booth_name": b["name"],
                       "rows": rows})
    with tx():
        for org_id, ids in by_org.items():  # 부스 소유 기관마다 한 건씩 남겨 각 기관이 볼 수 있게 한다
            audit(p, "export", None, f"명단 내보내기: 부스 {len(ids)}개",
                  org_id=org_id, detail={"booth_ids": ids})
    return {"booths": booths}
