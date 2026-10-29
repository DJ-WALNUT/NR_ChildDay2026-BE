def test_overview_includes_linked(client, db, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    host, me = make_org(name="재단"), make_org(name="나름")
    own_ev = make_event(me, name="어울마당"); make_booth(own_ev, name="내 행사 부스")
    ev = make_event(host, name="난장")
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", me, "t")).lastrowid
    b = make_booth(ev, partner_id=pid, name="난장 나름 부스"); make_booth(ev, name="재단 부스")
    make_reservation(b, "가"); make_reservation(b, "나", code="5678", status="noshow")
    make_user("kim", org_id=me); login_as(client, "kim")
    o = api(client, "GET", f"/api/admin/orgs/{me}/overview").json
    assert [g["event"]["name"] for g in o["owned"]] == ["어울마당"]
    assert o["linked"][0]["host"]["name"] == "재단" and [x["name"] for x in o["linked"][0]["booths"]] == ["난장 나름 부스"]
    assert o["totals"]["booths"] == 2 and o["totals"]["total"] == 1

def test_today_uses_kst(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation, monkeypatch):
    import datetime as dt, app.timeutil as tu
    org = make_org(); make_user("kim", org_id=org); login_as(client, "kim"); b = make_booth(make_event(org))
    utc = lambda *a: dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp()
    make_reservation(b, "어제", created_at=utc(2026, 5, 4, 14, 59))     # KST 5/4 23:59
    make_reservation(b, "오늘", code="5678", created_at=utc(2026, 5, 4, 15, 1))  # KST 5/5 00:01
    monkeypatch.setattr(tu, "now", lambda: utc(2026, 5, 5, 3, 0))
    assert api(client, "GET", f"/api/admin/orgs/{org}/overview").json["totals"]["today"] == 1

def test_audit_visibility(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    a, b = make_org(), make_org()
    make_user("hong", org_id=a); make_user("kim", org_id=b); make_user("root", superadmin=True)
    login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{a}/events", json={"name": "A행사"}).json["event"]["id"]
    login_as(client, "kim")
    own = api(client, "GET", f"/api/admin/orgs/{b}/audit").json["items"]
    assert [i["action"] for i in own] == ["auth.login"]           # A 기관의 event.create는 안 보인다
    assert api(client, "GET", f"/api/admin/orgs/{a}/audit").status_code == 404
    login_as(client, "root")
    items = api(client, "GET", f"/api/admin/super/audit?action=event.").json["items"]
    assert [i["summary"] for i in items] and items[0]["actor_label"] == "hong(hong)"


def test_bad_query_params_422(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("kim", org_id=org); login_as(client, "kim")
    assert api(client, "GET", f"/api/admin/orgs/{org}/audit?before=x").status_code == 422
    assert api(client, "GET", f"/api/admin/orgs/{org}/audit?limit=x").status_code == 422
    make_user("root", superadmin=True); login_as(client, "root")
    assert api(client, "GET", "/api/admin/super/audit?org_id=x").status_code == 422
