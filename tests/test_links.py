def _setup(db, make_org, make_event, make_booth):
    host, linked = make_org(), make_org(); ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", linked, "PTOKEN")).lastrowid
    mine, free = make_booth(ev, partner_id=pid, name="내 부스"), make_booth(ev, name="남의 부스")
    tok = lambda b: db.execute("SELECT manage_token FROM booths WHERE id=?", (b,)).fetchone()[0]
    return dict(host=host, linked=linked, ev=ev, pid=pid, mine=mine, free=free, tok=tok)

def test_booth_link_scope(client, db, make_org, make_event, make_booth, make_reservation):
    s = _setup(db, make_org, make_event, make_booth)
    t = s["tok"](s["free"])
    assert client.get(f"/api/link/booth/{t}").json["booth"]["name"] == "남의 부스"
    assert client.post(f"/api/link/booth/{t}/toggle").json["is_active"] is False
    assert client.patch(f"/api/link/booth/{t}", json={"event_id": s["ev"]}).status_code == 403
    foreign = make_reservation(s["mine"], "가")
    assert client.delete(f"/api/link/booth/{t}/reservations/{foreign}").status_code == 404

def test_partner_link_scope(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    g = client.get("/api/link/partner/PTOKEN").json
    assert [b["name"] for b in g["booths"]] == ["내 부스"]
    assert client.post(f"/api/link/partner/PTOKEN/booths/{s['mine']}/toggle").status_code == 200
    assert client.post(f"/api/link/partner/PTOKEN/booths/{s['free']}/toggle").status_code == 404
    assert client.post("/api/link/partner/PTOKEN/stats", json={"booth_ids": [s["free"]], "mode": "simple"}).status_code == 404
    assert client.post("/api/link/partner/PTOKEN/stats", json={"mode": "simple"}).status_code == 200

def test_link_dies_after_rotate_delete_or_org_deactivate(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    t = s["tok"](s["free"])
    db.execute("UPDATE booths SET manage_token='NEW' WHERE id=?", (s["free"],))
    assert client.get(f"/api/link/booth/{t}").status_code == 404
    db.execute("DELETE FROM partners WHERE id=?", (s["pid"],))
    assert client.get("/api/link/partner/PTOKEN").status_code == 404
    db.execute("UPDATE organizations SET is_active=0 WHERE id=?", (s["host"],))
    assert client.get("/api/link/booth/NEW").status_code == 404

def test_link_actions_audited_with_label(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    client.post(f"/api/link/booth/{s['tok'](s['free'])}/toggle")
    row = db.execute("SELECT actor, actor_label, org_id FROM audit_log WHERE action='booth.toggle'").fetchone()
    assert tuple(row) == ("booth_link", "부스 링크: 남의 부스", s["host"])

def test_linked_org_loses_access_after_unlink(client, db, make_org, make_event, make_booth, make_user, login_as, api):
    s = _setup(db, make_org, make_event, make_booth)
    make_user("kim", org_id=s["linked"]); login_as(client, "kim")
    assert api(client, "GET", f"/api/admin/booths/{s['mine']}").status_code == 200
    db.execute("UPDATE partners SET linked_org_id=NULL WHERE id=?", (s["pid"],))
    assert api(client, "GET", f"/api/admin/booths/{s['mine']}").status_code == 404

def test_partner_link_reservations_and_export(client, db, make_org, make_event, make_booth, make_reservation):
    s = _setup(db, make_org, make_event, make_booth)
    rid = make_reservation(s["mine"], "가")
    base = f"/api/link/partner/PTOKEN/booths/{s['mine']}"
    assert len(client.get(base + "/reservations").json["reservations"]) == 1
    assert client.patch(f"{base}/reservations/{rid}", json={"status": "noshow"}).json["reservation"]["status"] == "noshow"
    assert client.patch(base, json={"partner_id": None}).status_code == 403
    assert len(client.post("/api/link/partner/PTOKEN/export", json={}).json["booths"]) == 1
    assert client.post("/api/link/partner/PTOKEN/stats", json={"mode": "bad"}).status_code == 422
    assert client.delete(f"{base}/reservations").json["deleted"] == 1

def test_partner_link_with_zero_booths(client, db, make_org, make_event):
    host, linked = make_org(), make_org(); ev = make_event(host)
    db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
               (ev, "빈단체", linked, "EMPTY"))
    r = client.post("/api/link/partner/EMPTY/stats", json={"mode": "simple"})
    assert r.status_code == 200 and r.json["total"] == 0 and len(r.json["rows"]) == 6
    r = client.post("/api/link/partner/EMPTY/export", json={})
    assert r.status_code == 200 and r.json == {"booths": []}
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='export'").fetchone()[0] == 0

def test_booth_link_wins_over_session_cookie(client, db, make_org, make_event, make_booth, make_user, login_as):
    s = _setup(db, make_org, make_event, make_booth)
    other = make_org(); make_user("zed", org_id=other); login_as(client, "zed")
    r = client.post(f"/api/link/booth/{s['tok'](s['free'])}/toggle")
    assert r.status_code == 200
    row = db.execute("SELECT actor FROM audit_log WHERE action='booth.toggle'").fetchone()
    assert row[0] == "booth_link"
