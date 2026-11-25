"""행사별 연령대 이름·선택지."""
import pytest

DEFAULT = ["0~8세", "9~13세", "14~16세", "17~19세", "20~24세", "24세 이상"]


@pytest.fixture
def setup(client, make_org, make_user, login_as, make_event, make_booth):
    org = make_org(slug="nareum")
    ev = make_event(org)
    booth = make_booth(ev, mode="fcfs")
    other = make_booth(make_event(org, name="다른 행사"), mode="fcfs")
    make_user("hong", org_id=org)
    login_as(client, "hong")
    return ev, booth, other


def test_default_and_update_only_that_event(client, db, api, setup):
    ev, booth, other = setup
    e = api(client, "GET", f"/api/admin/events/{ev}").json["event"]
    assert e["age_label"] == "연령대" and e["age_options"] == DEFAULT
    r = api(client, "PATCH", f"/api/admin/events/{ev}", json={"age_label": " 학년 ", "age_options": [" 초등 ", "중등"]})
    assert r.status_code == 200
    assert r.json["event"]["age_label"] == "학년" and r.json["event"]["age_options"] == ["초등", "중등"]
    assert r.json["event"]["name"] == "행사"
    mine = client.get(f"/api/public/orgs/nareum/booths/{booth}").json["event"]
    assert (mine["age_label"], mine["age_options"]) == ("학년", ["초등", "중등"])
    assert client.get(f"/api/public/orgs/nareum/booths/{other}").json["event"]["age_options"] == DEFAULT
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='event.update'").fetchone()[0] == 1


@pytest.mark.parametrize("body", [
    {"age_label": ""}, {"age_label": "가" * 21}, {"age_label": None}, {"age_label": "가\n나"},
    {"age_options": []}, {"age_options": "초등"}, {"age_options": ["a", " a "]}, {"age_options": [" "]},
    {"age_options": [1]}, {"age_options": ["가" * 31]}, {"age_options": [str(i) for i in range(31)]},
])
def test_invalid(client, api, setup, body):
    r = api(client, "PATCH", f"/api/admin/events/{setup[0]}", json=body)
    assert r.status_code == 422 and set(r.json["fields"]) == set(body)


def test_reserve_follows_event_options(client, api, setup):
    ev, booth, other = setup
    api(client, "PATCH", f"/api/admin/events/{ev}", json={"age_label": "학년", "age_options": ["초등"]})

    def send(b, age, name):
        return client.post(f"/api/public/booths/{b}/reservations", json={
            "name": name, "gender": "남", "age_group": age, "code": "1234"})

    assert send(booth, "초등", "가").status_code == 201
    r = send(booth, "9~13세", "나")
    assert r.status_code == 422 and r.json["fields"] == {"age_group": "학년 항목을 선택해 주세요."}
    assert send(booth, ["초등"], "다").status_code == 422
    assert send(other, "9~13세", "라").status_code == 201


def test_stats_keep_removed_options(client, api, setup, make_reservation):
    ev, booth, other = setup
    make_reservation(booth, "가", age_group="9~13세")
    api(client, "PATCH", f"/api/admin/events/{ev}", json={"age_label": "학년", "age_options": ["초등", "중등"]})
    make_reservation(booth, "나", age_group="초등")
    s = api(client, "POST", "/api/admin/stats", json={"booth_ids": [booth], "mode": "strict"}).json
    assert s["age_label"] == "학년" and s["total"] == 2
    assert [(r["age_group"], r["total"], r["removed"]) for r in s["rows"]] == [
        ("초등", 1, False), ("중등", 0, False), ("9~13세", 1, True)]
    mixed = api(client, "POST", "/api/admin/stats", json={"booth_ids": [booth, other], "mode": "simple"}).json
    assert mixed["age_label"] == "연령대" and mixed["total"] == 2
