def test_create_and_list(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    r = api(client, "POST", f"/api/admin/events/{ev}/booths",
            json={"name": " 페이스페인팅 ", "mode": "fcfs", "total_limit": 100})
    assert r.status_code == 201 and r.json["booth"]["name"] == "페이스페인팅"
    assert len(r.json["booth"]["manage_token"]) == 24
    r = api(client, "GET", f"/api/admin/events/{ev}/booths")
    assert [b["name"] for b in r.json["booths"]] == ["페이스페인팅"]


def test_invalid_hours(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    r = api(client, "POST", f"/api/admin/events/{ev}/booths",
            json={"name": "x", "mode": "time", "start_hour": 16, "end_hour": 11})
    assert r.status_code == 422 and "end_hour" in r.json["fields"]


def test_bulk_all_or_nothing(client, db, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    rows = [{"name": "a", "mode": "fcfs", "total_limit": 10}, {"name": "", "mode": "fcfs"}]
    r = api(client, "POST", f"/api/admin/events/{ev}/booths/bulk", json={"rows": rows})
    assert r.status_code == 422 and list(r.json["fields"]["rows"]) == ["1"]
    assert db.execute("SELECT COUNT(*) FROM booths").fetchone()[0] == 0
    r = api(client, "POST", f"/api/admin/events/{ev}/booths/bulk", json={"rows": rows[:1]})
    assert r.status_code == 201 and r.json == {"created": 1}
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='booth.bulk_create'").fetchone()[0] == 1


def test_create_partner_must_be_in_event(client, db, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    e1, e2 = make_event(org), make_event(org)
    pid = db.execute("INSERT INTO partners (event_id,name,access_token,created_at) VALUES (?,?,?,0)",
                     (e2, "p", "t")).lastrowid
    r = api(client, "POST", f"/api/admin/events/{e1}/booths", json={"name": "x", "partner_id": pid})
    assert r.status_code == 422 and "partner_id" in r.json["fields"]


def test_other_org_hidden(client, make_org, make_user, login_as, api, make_event, make_booth):
    a, b = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    ev = make_event(b); bo = make_booth(ev)
    assert api(client, "GET", f"/api/admin/events/{ev}/booths").status_code == 404
    assert api(client, "GET", f"/api/admin/booths/{bo}").status_code == 404


def test_linked_org_can_manage_not_own(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    host, linked = make_org(), make_org(); ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", linked, "t")).lastrowid
    b = make_booth(ev, partner_id=pid); make_user("kim", org_id=linked); login_as(client, "kim")
    assert api(client, "POST", f"/api/admin/booths/{b}/toggle").status_code == 200
    assert api(client, "PATCH", f"/api/admin/booths/{b}", json={"total_limit": 5}).status_code == 200
    assert api(client, "PATCH", f"/api/admin/booths/{b}", json={"total_limit": 5, "partner_id": None}).status_code == 403
    assert api(client, "DELETE", f"/api/admin/booths/{b}").status_code == 403
    assert api(client, "POST", f"/api/admin/booths/{b}/rotate-token").status_code == 403
    assert api(client, "POST", "/api/admin/booths/batch-status", json={"booth_ids": [b], "is_active": False}).status_code == 403


def test_move_booth_clears_partner(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    e1, e2 = make_event(org), make_event(org)
    pid = db.execute("INSERT INTO partners (event_id,name,access_token,created_at) VALUES (?,?,?,0)", (e1, "p", "t")).lastrowid
    b = make_booth(e1, partner_id=pid)
    r = api(client, "PATCH", f"/api/admin/booths/{b}", json={"event_id": e2})
    assert r.json["booth"]["event_id"] == e2 and r.json["booth"]["partner_id"] is None


def test_move_to_other_org_event_rejected(client, make_org, make_user, login_as, api, make_event, make_booth):
    a, b = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    bo = make_booth(make_event(a))
    r = api(client, "PATCH", f"/api/admin/booths/{bo}", json={"event_id": make_event(b)})
    assert r.status_code == 422 and "event_id" in r.json["fields"]


def test_settings_change_keeps_existing_reservations(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, mode="time", start_hour=11, end_hour=12, slots_per_hour=3, limit_per_slot=5)
    make_reservation(b, "가", slot="12시 C타임")
    r = api(client, "PATCH", f"/api/admin/booths/{b}", json={"slots_per_hour": 2, "end_hour": 11})
    assert r.status_code == 200 and r.json["booth"]["count"] == 1
    labels = [s["label"] for s in r.json["booth"]["availability"]["slots"]]
    assert labels == ["11시 A타임", "11시 B타임"]


def test_patch_audit_summary(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, name="페이스페인팅", mode="fcfs", total_limit=100)
    api(client, "PATCH", f"/api/admin/booths/{b}", json={"total_limit": 120})
    row = db.execute("SELECT summary, org_id FROM audit_log WHERE action='booth.update'").fetchone()
    assert row["summary"] == "페이스페인팅 설정 변경: 정원 100 → 120" and row["org_id"] == org


def test_patch_validation(client, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, start_hour=11, end_hour=16)
    r = api(client, "PATCH", f"/api/admin/booths/{b}", json={"end_hour": 9, "slots_per_hour": 7, "name": " "})
    assert r.status_code == 422 and {"end_hour", "slots_per_hour", "name"} <= set(r.json["fields"])


def test_toggle_delete_rotate_batch(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b1, b2 = make_booth(ev), make_booth(ev)
    assert api(client, "POST", f"/api/admin/booths/{b1}/toggle").json == {"is_active": False}
    r = api(client, "POST", "/api/admin/booths/batch-status", json={"booth_ids": [b1, b2], "is_active": False})
    assert r.json == {"updated": 2}
    assert db.execute("SELECT COUNT(*) FROM booths WHERE is_active=0").fetchone()[0] == 2
    old = db.execute("SELECT manage_token FROM booths WHERE id=?", (b1,)).fetchone()[0]
    new = api(client, "POST", f"/api/admin/booths/{b1}/rotate-token").json["manage_token"]
    assert new != old and len(new) == 24
    assert api(client, "DELETE", f"/api/admin/booths/{b1}").status_code == 204
    assert db.execute("SELECT COUNT(*) FROM booths").fetchone()[0] == 1
    assert {"booth.toggle", "booth.batch_status", "booth.rotate_token", "booth.delete"} <= {
        r[0] for r in db.execute("SELECT action FROM audit_log")}


def test_batch_status_all_or_nothing(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    a, b = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    mine, other = make_booth(make_event(a)), make_booth(make_event(b))
    r = api(client, "POST", "/api/admin/booths/batch-status", json={"booth_ids": [mine, other], "is_active": False})
    assert r.status_code == 404
    assert db.execute("SELECT is_active FROM booths WHERE id=?", (mine,)).fetchone()[0] == 1


def _linked_setup(db, make_org, make_user, login_as, client, make_event, make_booth):
    host, me = make_org(name="재단"), make_org(name="나름")
    ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", me, "t")).lastrowid
    partner_booth = make_booth(ev, partner_id=pid)
    own_booth = make_booth(make_event(me))
    make_user("kim", org_id=me); login_as(client, "kim")
    return host, me, own_booth, partner_booth


def test_export_audited_in_every_owning_org(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    import json
    host, me, own, partner = _linked_setup(db, make_org, make_user, login_as, client, make_event, make_booth)
    assert api(client, "POST", "/api/admin/export", json={"booth_ids": [own, partner]}).status_code == 200
    rows = db.execute("SELECT org_id, detail FROM audit_log WHERE action='export'").fetchall()
    by_org = {r["org_id"]: json.loads(r["detail"])["booth_ids"] for r in rows}
    assert by_org == {me: [own], host: [partner]}


def test_batch_status_audited_in_every_owning_org(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    import json
    host, me, own, partner = _linked_setup(db, make_org, make_user, login_as, client, make_event, make_booth)
    # 연계 단체는 own 권한이 없으므로 배치는 슈퍼관리자로
    make_user("root", superadmin=True)
    c2 = client.application.test_client(); login_as(c2, "root")
    r = api(c2, "POST", "/api/admin/booths/batch-status", json={"booth_ids": [own, partner], "is_active": False})
    assert r.status_code == 200
    rows = db.execute("SELECT org_id, detail FROM audit_log WHERE action='booth.batch_status'").fetchall()
    assert {r["org_id"]: json.loads(r["detail"])["booth_ids"] for r in rows} == {me: [own], host: [partner]}


def test_toggle_flips_atomically(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    import json
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    b = make_booth(make_event(org))
    from app.access import load_booth
    from app.booth_ops import toggle
    from app.principal import Principal
    with client.application.app_context():
        stale = load_booth(b)
    db.execute("UPDATE booths SET is_active = 0 WHERE id=?", (b,))  # 다른 요청이 먼저 끔
    with client.application.app_context():
        assert toggle(Principal(kind="user", label="x", user_id=None, org_id=org, is_superadmin=False), stale) is True
    assert db.execute("SELECT is_active FROM booths WHERE id=?", (b,)).fetchone()[0] == 1
    d = db.execute("SELECT detail FROM audit_log WHERE action='booth.toggle'").fetchone()[0]
    assert json.loads(d) == {"is_active": True}
