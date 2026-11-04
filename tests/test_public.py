import pytest

from app.config import _positive

def test_org_listing_hides_inactive(client, make_org, make_event, make_booth):
    org = make_org(slug="nareum"); ev = make_event(org, name="어울마당")
    make_booth(ev, name="열림"); make_booth(ev, name="닫힘", is_active=0); make_event(org, name="빈 행사")
    j = client.get("/api/public/orgs/nareum").json
    assert [e["name"] for e in j["events"]] == ["어울마당"]
    assert [b["name"] for b in j["events"][0]["booths"]] == ["열림"]
    assert client.get("/api/public/orgs/nope").status_code == 404

def test_inactive_org_is_hidden(client, make_org, make_event, make_booth):
    org = make_org(slug="off", active=False)
    b = make_booth(make_event(org))
    assert client.get("/api/public/orgs/off").status_code == 404
    assert client.get(f"/api/public/orgs/off/booths/{b}").status_code == 404
    assert client.get(f"/api/public/booths/{b}/home").status_code == 404
    body = {"name": "a", "gender": "남", "age_group": "9~13세", "code": "1234"}
    assert client.post(f"/api/public/booths/{b}/reservations", json=body).status_code == 404

def test_booth_must_belong_to_slug(client, make_org, make_event, make_booth):
    make_org(slug="nareum"); other = make_org(slug="gmyouth")
    b = make_booth(make_event(other))
    assert client.get(f"/api/public/orgs/nareum/booths/{b}").status_code == 404
    assert client.get(f"/api/public/orgs/gmyouth/booths/{b}").json["booth"]["id"] == b

def test_reserve_normalizes_and_rejects_duplicate(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=10)
    body = {"name": " 홍길동 ", "gender": "남", "age_group": "9~13세", "code": "12-34"}
    r = client.post(f"/api/public/booths/{b}/reservations", json=body)
    assert r.status_code == 201 and r.json["slot"] == "선착순" and r.json["status"] == "normal"
    assert client.post(f"/api/public/booths/{b}/reservations", json={**body, "name": "홍길동"}).json["error"] == "duplicate"
    assert client.post(f"/api/public/booths/{b}/reservations", json={**body, "code": "１２３４"}).status_code == 422

def test_reserve_collects_all_field_errors(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()))
    r = client.post(f"/api/public/booths/{b}/reservations", json={"name": " ", "gender": "x", "age_group": "y", "code": "1"})
    assert r.status_code == 422 and set(r.json["fields"]) == {"name", "gender", "age_group", "code"}

def test_waitlist_and_closed(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=1, use_waitlist=1)
    mk = lambda i: client.post(f"/api/public/booths/{b}/reservations",
                               json={"name": f"p{i}", "gender": "남", "age_group": "9~13세", "code": f"000{i}"})
    assert mk(1).json["status"] == "normal" and mk(2).json["status"] == "waiting"
    c = make_booth(make_event(make_org(name="o2", slug="o2")), is_active=0)
    r = client.post(f"/api/public/booths/{c}/reservations",
                    json={"name": "p", "gender": "남", "age_group": "9~13세", "code": "0001", "slot": "11시 A타임"})
    assert r.status_code == 409 and r.json["error"] == "closed"

def test_lookup_and_cancel_only_own(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()))
    make_reservation(b, "홍길동", code="1234"); make_reservation(b, "김철수", code="5678", status="completed")
    r = client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동 ", "code": "12-34"})
    assert r.status_code == 200 and set(r.json) == {"name", "slot", "status", "booth_name"}
    assert client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동", "code": "9999"}).status_code == 404
    assert client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동", "code": "１２３４"}).status_code == 422
    assert client.post(f"/api/public/booths/{b}/cancel", json={"name": "김철수", "code": "5678"}).json["error"] == "not_cancellable"
    assert client.post(f"/api/public/booths/{b}/cancel", json={"name": "홍길동", "code": "1234"}).status_code == 204
    assert client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동", "code": "1234"}).status_code == 404

def test_lookup_rate_limit(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()))
    codes = [client.post(f"/api/public/booths/{b}/lookup", json={"name": "x", "code": "0000"}).status_code for _ in range(21)]
    assert codes[:20] == [404] * 20 and codes[20] == 429

def test_concurrent_reservations_never_exceed(app, make_org, make_event, make_booth, db):
    import threading
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=5)
    results = []
    def go(i):
        c = app.test_client()
        r = c.post(f"/api/public/booths/{b}/reservations",
                   json={"name": f"p{i}", "gender": "여", "age_group": "0~8세", "code": f"{i:04d}"},
                   environ_base={"REMOTE_ADDR": f"10.0.0.{i}"})
        results.append(r.status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(201) == 5 and results.count(409) == 15
    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 5

def test_home_redirect_slug(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org(slug="gmyouth")))
    assert client.get(f"/api/public/booths/{b}/home").json == {"slug": "gmyouth"}

def test_code_rejects_mixed_fullwidth_digits(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=10)
    body = {"name": "a", "gender": "남", "age_group": "9~13세"}
    r = client.post(f"/api/public/booths/{b}/reservations", json={**body, "code": "１2345"})
    assert r.status_code == 422 and "code" in r.json["fields"]
    assert client.post(f"/api/public/booths/{b}/reservations", json={**body, "code": "12-34"}).status_code == 201

def test_closed_booth_wins_over_duplicate(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=10, is_active=0)
    make_reservation(b, "홍길동", code="1234", slot="선착순")
    r = client.post(f"/api/public/booths/{b}/reservations",
                    json={"name": "홍길동", "gender": "남", "age_group": "9~13세", "code": "1234"})
    assert r.status_code == 409 and r.json["error"] == "closed"

def _apply(client, booth_id, name, code, slot=None):
    return client.post(f"/api/public/booths/{booth_id}/reservations",
                       json={"name": name, "gender": "남", "age_group": "9~13세", "code": code, "slot": slot})

def test_unknown_slot_does_not_reveal_identity(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()), start_hour=11, end_hour=12, slots_per_hour=1)
    make_reservation(b, "홍길동", code="1234", slot="11시")
    match, miss = _apply(client, b, "홍길동", "1234", "없는 시간"), _apply(client, b, "홍길동", "9999", "없는 시간")
    assert match.status_code == miss.status_code == 422
    assert match.json == miss.json

def test_full_slot_does_not_reveal_identity(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()), start_hour=11, end_hour=12, slots_per_hour=1, limit_per_slot=1)
    make_reservation(b, "홍길동", code="1234", slot="11시")
    match, miss = _apply(client, b, "홍길동", "1234", "11시"), _apply(client, b, "홍길동", "9999", "11시")
    assert match.status_code == miss.status_code == 409
    assert match.json == miss.json and match.json["error"] == "full"

def test_full_fcfs_does_not_reveal_identity(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=1)
    make_reservation(b, "홍길동", code="1234", slot="선착순")
    match, miss = _apply(client, b, "홍길동", "1234"), _apply(client, b, "홍길동", "9999")
    assert match.json == miss.json and match.json["error"] == "full"

def test_waitlist_still_reports_duplicate(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=1, use_waitlist=1)
    make_reservation(b, "홍길동", code="1234", slot="선착순")
    r = _apply(client, b, "홍길동", "1234")
    assert r.status_code == 409 and r.json["error"] == "duplicate"
    assert _apply(client, b, "김철수", "1234").json["status"] == "waiting"
    assert _apply(client, b, "김철수", "1234").json["error"] == "duplicate"

@pytest.mark.parametrize("path, body", [
    ("reservations", {"name": "x", "gender": "남", "age_group": "9~13세", "code": "0000"}),
    ("lookup", {"name": "x", "code": "0000"}),
    ("cancel", {"name": "x", "code": "0000"}),
])
def test_unknown_booth_writes_no_rate_events(client, db, path, body):
    codes = [client.post(f"/api/public/booths/{i}/{path}", json=body).status_code for i in range(9000, 9040)]
    assert codes == [404] * 40
    assert db.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0] == 0

def test_inactive_org_booth_writes_no_rate_events(client, db, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org(active=False)))
    assert client.post(f"/api/public/booths/{b}/lookup", json={"name": "x", "code": "0000"}).status_code == 404
    assert db.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0] == 0

def test_lookup_and_cancel_share_counter(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()))
    body = {"name": "x", "code": "0000"}
    codes = [client.post(f"/api/public/booths/{b}/{'lookup' if i % 2 else 'cancel'}", json=body).status_code
             for i in range(21)]
    assert codes[:20] == [404] * 20 and codes[20] == 429


def test_limits_are_per_booth(client, make_org, make_event, make_booth):
    ev = make_event(make_org())
    a, b = make_booth(ev), make_booth(ev)
    body = {"name": "x", "code": "0000"}
    assert [client.post(f"/api/public/booths/{a}/lookup", json=body).status_code for _ in range(21)][20] == 429
    assert client.post(f"/api/public/booths/{b}/lookup", json=body).status_code == 404
    assert client.post(f"/api/public/booths/{b}/cancel", json=body).status_code == 404


def test_reserve_limit_is_per_booth(client, make_org, make_event, make_booth):
    ev = make_event(make_org())
    a, b = make_booth(ev), make_booth(ev)
    assert [client.post(f"/api/public/booths/{a}/reservations", json={}).status_code for _ in range(31)][30] == 429
    assert client.post(f"/api/public/booths/{b}/reservations", json={}).status_code == 422


def test_visitor_limits_come_from_config(app, client, make_org, make_event, make_booth):
    app.config.update(VISITOR_LOOKUP_LIMIT=2, VISITOR_RESERVE_LIMIT=1, LINK_MISS_LIMIT=1)
    b = make_booth(make_event(make_org()))
    body = {"name": "x", "code": "0000"}
    assert [client.post(f"/api/public/booths/{b}/lookup", json=body).status_code for _ in range(3)] == [404, 404, 429]
    assert [client.post(f"/api/public/booths/{b}/reservations", json={}).status_code for _ in range(2)] == [422, 429]
    assert [client.get("/api/link/booth/nope").status_code for _ in range(2)] == [404, 429]


@pytest.mark.parametrize("raw, expected", [(None, 30), ("", 30), ("abc", 30), ("0", 30), ("-5", 30), ("120", 120)])
def test_positive_int_setting(monkeypatch, raw, expected):
    monkeypatch.delenv("X_LIMIT", raising=False)
    if raw is not None:
        monkeypatch.setenv("X_LIMIT", raw)
    assert _positive("X_LIMIT", 30) == expected
