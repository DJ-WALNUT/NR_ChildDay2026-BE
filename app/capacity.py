"""정원 계산. 점유 상태(normal, completed)만 자리를 차지한다."""

from __future__ import annotations

from typing import Mapping

from .errors import ApiError, ValidationError

OCCUPYING = ("normal", "completed")
FCFS_SLOT = "선착순"


def slot_labels(start_hour: int, end_hour: int, slots_per_hour: int) -> list[str]:
    labels = []
    for h in range(start_hour, end_hour + 1):
        if slots_per_hour == 1:
            labels.append(f"{h}시")
        else:
            labels.extend(f"{h}시 {chr(65 + i)}타임" for i in range(slots_per_hour))
    return labels


def occupancy(db, booth_id: int) -> dict[str, int]:
    marks = ",".join("?" for _ in OCCUPYING)
    rows = db.execute(
        f"SELECT slot, COUNT(*) AS n FROM reservations WHERE booth_id=? AND status IN ({marks}) GROUP BY slot",
        (booth_id, *OCCUPYING),
    ).fetchall()
    return {r["slot"]: r["n"] for r in rows}


def _remaining(limit: int, used: int) -> tuple[int | None, bool]:
    if limit == 0:
        return None, False
    rem = max(limit - used, 0)
    return rem, rem == 0


def availability(booth: Mapping, occ: dict[str, int]) -> dict:
    active = bool(booth["is_active"])
    if booth["mode"] == "fcfs":
        rem, full = _remaining(booth["total_limit"], occ.get(FCFS_SLOT, 0))
        closed = not active or (not booth["use_waitlist"] and full)
        return {"remaining": rem, "full": full, "is_closed": closed}
    slots = []
    for label in slot_labels(booth["start_hour"], booth["end_hour"], booth["slots_per_hour"]):
        rem, full = _remaining(booth["limit_per_slot"], occ.get(label, 0))
        slots.append({"label": label, "remaining": rem, "full": full})
    all_full = all(s["full"] for s in slots)
    closed = not active or not slots or (not booth["use_waitlist"] and all_full)
    return {"slots": slots, "is_closed": closed}


def decide_status(booth: Mapping, occ: dict[str, int], slot: str | None) -> tuple[str, str]:
    if not booth["is_active"]:
        raise ApiError(409, "closed")
    a = availability(booth, occ)
    if booth["mode"] == "fcfs":
        slot, full = FCFS_SLOT, a["full"]
    else:
        match = next((s for s in a["slots"] if s["label"] == slot), None)
        if match is None:
            raise ValidationError({"slot": "선택할 수 없는 시간입니다."})
        full = match["full"]
    if full:
        if booth["use_waitlist"]:
            return slot, "waiting"
        raise ApiError(409, "full")
    return slot, "normal"
