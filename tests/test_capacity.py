import pytest

from app.capacity import availability, decide_status, slot_labels
from app.errors import ApiError, ValidationError


def B(**kw):
    base = dict(mode="time", use_waitlist=0, total_limit=0, start_hour=11, end_hour=12,
                slots_per_hour=2, limit_per_slot=2, is_active=1)
    return {**base, **kw}


def test_labels():
    assert slot_labels(11, 12, 2) == ["11시 A타임", "11시 B타임", "12시 A타임", "12시 B타임"]
    assert slot_labels(11, 11, 1) == ["11시"]


def test_time_availability_and_orphan_slot_ignored():
    a = availability(B(), {"11시 A타임": 2, "15시 C타임": 9})
    assert a["slots"][0] == {"label": "11시 A타임", "remaining": 0, "full": True}
    assert a["slots"][1]["remaining"] == 2 and a["is_closed"] is False


def test_unlimited_slot_never_full():
    a = availability(B(limit_per_slot=0), {"11시 A타임": 50})
    assert a["slots"][0] == {"label": "11시 A타임", "remaining": None, "full": False}


def test_closed_when_all_full_without_waitlist():
    occ = {l: 2 for l in slot_labels(11, 12, 2)}
    assert availability(B(), occ)["is_closed"] is True
    assert availability(B(use_waitlist=1), occ)["is_closed"] is False


def test_closed_when_inactive_or_no_slots():
    assert availability(B(is_active=0), {})["is_closed"] is True
    assert availability(B(start_hour=13, end_hour=12), {})["is_closed"] is True


def test_decide():
    occ = {"11시 A타임": 2}
    with pytest.raises(ApiError) as e:
        decide_status(B(), occ, "11시 A타임")
    assert e.value.code == "full"
    assert decide_status(B(use_waitlist=1), occ, "11시 A타임") == ("11시 A타임", "waiting")
    assert decide_status(B(), occ, "11시 B타임") == ("11시 B타임", "normal")
    with pytest.raises(ValidationError):
        decide_status(B(), occ, "15시 C타임")
    with pytest.raises(ApiError) as e:
        decide_status(B(is_active=0), {}, "11시 B타임")
    assert e.value.code == "closed"


def test_fcfs():
    f = B(mode="fcfs", total_limit=3)
    assert availability(f, {"선착순": 3}) == {"remaining": 0, "full": True, "is_closed": True}
    assert decide_status(f, {"선착순": 2}, "아무거나") == ("선착순", "normal")
    assert availability(B(mode="fcfs", total_limit=0), {"선착순": 99})["full"] is False


def test_occupancy_excludes_noshow_waiting(app, db, make_org, make_event, make_booth, make_reservation):
    from app.capacity import occupancy

    booth = make_booth(make_event(make_org()))
    for name, status in [("가", "normal"), ("나", "completed"), ("다", "noshow"), ("라", "waiting")]:
        make_reservation(booth, name, status=status)
    assert occupancy(db, booth) == {"11시 A타임": 2}
