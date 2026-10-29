def test_stats_simple_and_strict(app, make_org, make_event, make_booth, make_reservation):
    from app.booth_ops import stats
    ev = make_event(make_org()); b1, b2 = make_booth(ev), make_booth(ev)
    make_reservation(b1, "가", gender="남", age_group="9~13세")
    make_reservation(b2, "가", gender="남", age_group="9~13세")          # 같은 사람
    make_reservation(b2, "나", gender="여", age_group="0~8세", status="noshow")
    with app.app_context():
        s = stats([b1, b2], "simple"); t = stats([b1, b2], "strict")
    assert s["total"] == 2 and s["male"] == 2 and s["female"] == 0
    assert t["total"] == 1
    assert [r["age_group"] for r in s["rows"]] == ["0~8세", "9~13세", "14~16세", "17~19세", "20~24세", "24세 이상"]


def test_noshow_frees_seat_and_waiting_promotion_warns(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, mode="fcfs", total_limit=1, use_waitlist=1)
    r1 = make_reservation(b, "가", slot="선착순"); r2 = make_reservation(b, "나", slot="선착순", status="waiting")
    assert api(client, "PATCH", f"/api/admin/reservations/{r2}", json={"status": "normal"}).json["over_capacity"] is True
    api(client, "PATCH", f"/api/admin/reservations/{r1}", json={"status": "noshow"})
    booth = api(client, "GET", f"/api/admin/booths/{b}").json["booth"]
    assert booth["availability"]["remaining"] == 0     # 나(normal) 1명 점유


def test_no_over_capacity_flag_when_room(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, mode="fcfs", total_limit=2, use_waitlist=1)
    r = make_reservation(b, "가", slot="선착순", status="waiting")
    res = api(client, "PATCH", f"/api/admin/reservations/{r}", json={"status": "normal"})
    assert res.json["reservation"]["status"] == "normal" and "over_capacity" not in res.json
    assert api(client, "PATCH", f"/api/admin/reservations/{r}", json={"status": "bogus"}).status_code == 422


def test_reservation_of_other_booth_404(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    a, b = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    foreign = make_reservation(make_booth(make_event(b)), "가")
    assert api(client, "PATCH", f"/api/admin/reservations/{foreign}", json={"status": "noshow"}).status_code == 404
    assert api(client, "DELETE", f"/api/admin/reservations/{foreign}").status_code == 404
    assert api(client, "PATCH", "/api/admin/reservations/99999", json={"status": "noshow"}).status_code == 404


def test_clear_and_export_audited(client, db, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, name="페인팅"); make_reservation(b, "가"); make_reservation(b, "나", code="5678")
    ex = api(client, "POST", "/api/admin/export", json={"booth_ids": [b]}).json
    assert ex["booths"][0]["booth_name"] == "페인팅" and len(ex["booths"][0]["rows"]) == 2
    assert api(client, "DELETE", f"/api/admin/booths/{b}/reservations").json == {"deleted": 2}
    actions = [r[0] for r in db.execute("SELECT action FROM audit_log ORDER BY id")]
    assert "export" in actions and "reservation.clear" in actions


def test_delete_one_audited_and_list_route(client, db, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev); r = make_reservation(b, "가")
    assert len(api(client, "GET", f"/api/admin/booths/{b}/reservations").json["reservations"]) == 1
    assert api(client, "DELETE", f"/api/admin/reservations/{r}").status_code == 204
    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='reservation.delete'").fetchone()[0] == 1


def test_stats_export_booth_ids_validation(client, make_org, make_user, login_as, api, make_event, make_booth):
    a, o = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    mine = make_booth(make_event(a)); foreign = make_booth(make_event(o))
    for path in ("stats", "export"):
        base = {"mode": "simple"} if path == "stats" else {}
        assert api(client, "POST", f"/api/admin/{path}", json={**base, "booth_ids": [mine, foreign]}).status_code == 404
        for bad in ([], [True], ["1"], None, list(range(1, 502))):
            assert api(client, "POST", f"/api/admin/{path}", json={**base, "booth_ids": bad}).status_code == 422
    assert api(client, "POST", "/api/admin/stats", json={"booth_ids": [mine], "mode": "x"}).status_code == 422
    assert api(client, "POST", "/api/admin/stats", json={"booth_ids": [mine, mine], "mode": "strict"}).json["total"] == 0


def test_list_order(app, make_org, make_event, make_booth, make_reservation):
    from app.booth_ops import list_reservations
    b = make_booth(make_event(make_org()), mode="time", start_hour=9, end_hour=11, slots_per_hour=2)
    for name, slot in [("c", "11시 A타임"), ("a", "9시 B타임"), ("b", "9시 A타임")]:
        make_reservation(b, name, slot=slot)
    with app.app_context():
        assert [r["name"] for r in list_reservations(b)] == ["b", "a", "c"]


def test_list_keeps_reservations_of_removed_slots(app, make_org, make_event, make_booth, make_reservation):
    from app.booth_ops import list_reservations
    b = make_booth(make_event(make_org()), mode="time", start_hour=9, end_hour=9, slots_per_hour=1)
    make_reservation(b, "old", slot="15시 C타임")
    with app.app_context():
        assert [r["name"] for r in list_reservations(b)] == ["old"]
