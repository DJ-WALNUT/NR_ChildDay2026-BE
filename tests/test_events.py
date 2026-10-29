def test_event_crud_and_cascade(client, db, make_org, make_user, login_as, api, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": "어울마당"}).json["event"]["id"]
    b = make_booth(ev); make_reservation(b, "가")
    assert api(client, "DELETE", f"/api/admin/events/{ev}").status_code == 204
    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0
    row = db.execute("SELECT summary, detail, org_id FROM audit_log WHERE action='event.delete'").fetchone()
    assert row["org_id"] == org and "어울마당" in row["summary"]
    assert '"booth_count": 1' in row["detail"] and '"reservation_count": 1' in row["detail"]


def test_event_list_update_validation_scope(client, make_org, make_user, login_as, api, make_booth):
    org, other = make_org(), make_org()
    make_user("hong", org_id=org); make_user("kim", org_id=other); login_as(client, "hong")
    e1 = api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": "하나"}).json["event"]["id"]
    e2 = api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": "둘"}).json["event"]["id"]
    make_booth(e2)
    evs = api(client, "GET", f"/api/admin/orgs/{org}/events").json["events"]
    assert [e["id"] for e in evs] == [e2, e1] and evs[0]["booth_count"] == 1 and evs[0]["partner_count"] == 0
    assert api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": " "}).status_code == 422
    assert api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": "x" * 101}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/events/{e1}", json={"name": "새"}).json["event"]["name"] == "새"
    assert api(client, "GET", f"/api/admin/orgs/{other}/events").status_code == 404
    assert api(client, "POST", f"/api/admin/orgs/{other}/events", json={"name": "x"}).status_code == 404
    login_as(client, "kim")
    assert api(client, "PATCH", f"/api/admin/events/{e1}", json={"name": "x"}).status_code == 404
    assert api(client, "DELETE", f"/api/admin/events/{e1}").status_code == 404
    assert api(client, "GET", f"/api/admin/events/{e1}/partners").status_code == 404


def test_partner_link_lifecycle(client, db, make_org, make_user, login_as, api, make_booth):
    host, linked = make_org(), make_org(); make_user("hong", org_id=host); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{host}/events", json={"name": "난장"}).json["event"]["id"]
    p = api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "나름", "linked_org_id": linked}).json["partner"]
    b = make_booth(ev, partner_id=p["id"])
    old = p["access_token"]
    new = api(client, "POST", f"/api/admin/partners/{p['id']}/rotate-token").json["access_token"]
    assert new != old
    lst = api(client, "GET", f"/api/admin/events/{ev}/partners").json["partners"]
    assert lst[0]["booth_count"] == 1 and lst[0]["access_token"] == new
    assert lst[0]["linked_org"]["id"] == linked
    assert api(client, "DELETE", f"/api/admin/partners/{p['id']}").status_code == 204
    assert db.execute("SELECT partner_id FROM booths WHERE id=?", (b,)).fetchone()[0] is None
    assert api(client, "DELETE", f"/api/admin/partners/{p['id']}").status_code == 404


def test_partner_patch_unlink(client, make_org, make_user, login_as, api):
    host, linked = make_org(), make_org(); make_user("hong", org_id=host); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{host}/events", json={"name": "난장"}).json["event"]["id"]
    p = api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "나름"}).json["partner"]
    assert p["linked_org"] is None
    r = api(client, "PATCH", f"/api/admin/partners/{p['id']}", json={"linked_org_id": linked})
    assert r.json["partner"]["linked_org"]["id"] == linked and r.json["partner"]["name"] == "나름"
    r = api(client, "PATCH", f"/api/admin/partners/{p['id']}", json={"linked_org_id": None})
    assert r.json["partner"]["linked_org"] is None
    r = api(client, "PATCH", f"/api/admin/partners/{p['id']}", json={"name": "바뀜"})
    assert r.json["partner"]["name"] == "바뀜"
    assert api(client, "PATCH", f"/api/admin/partners/{p['id']}", json={"linked_org_id": host}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/partners/{p['id']}", json={"linked_org_id": 99999}).status_code == 422
    assert api(client, "PATCH", "/api/admin/partners/99999", json={"name": "x"}).status_code == 404


def test_link_inactive_org_rejected(client, make_org, make_user, login_as, api):
    host, dead = make_org(), make_org(active=False); make_user("hong", org_id=host); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{host}/events", json={"name": "난장"}).json["event"]["id"]
    assert api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "x", "linked_org_id": dead}).status_code == 422
    assert api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "x", "linked_org_id": host}).status_code == 422
    ids = [o["id"] for o in api(client, "GET", f"/api/admin/orgs/{host}/link-targets").json["orgs"]]
    assert dead not in ids and host not in ids
