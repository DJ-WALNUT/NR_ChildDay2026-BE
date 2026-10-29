import re


def test_owner_gets_403_on_super(client, make_org, make_user, login_as, api):
    make_user("hong", org_id=make_org()); login_as(client, "hong")
    assert api(client, "GET", "/api/admin/super/orgs").status_code == 403

def test_create_and_rename_org(client, make_user, login_as, api):
    make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "POST", "/api/admin/super/orgs", json={"name": "나름", "slug": "nareum"})
    assert r.status_code == 201
    assert api(client, "POST", "/api/admin/super/orgs", json={"name": "x", "slug": "nareum"}).json["error"] == "slug_taken"
    assert api(client, "POST", "/api/admin/super/orgs", json={"name": "x", "slug": "manage"}).status_code == 422
    oid = r.json["org"]["id"]
    assert api(client, "PATCH", f"/api/admin/super/orgs/{oid}", json={"name": "나름센터"}).json["org"]["name"] == "나름센터"

def test_deactivate_org_logs_out_members(app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); make_user("root", superadmin=True)
    ch, cr = app.test_client(), app.test_client()
    login_as(ch, "hong"); login_as(cr, "root")
    api(cr, "PATCH", f"/api/admin/super/orgs/{org}", json={"is_active": False})
    assert ch.get("/api/admin/auth/me").status_code == 401

def test_backup_is_sqlite(client, make_user, login_as, api, db):
    make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "GET", "/api/admin/super/backup")
    assert r.status_code == 200 and r.data[:16] == b"SQLite format 3\x00"
    assert r.mimetype == "application/octet-stream"
    assert re.search(r"nrbooth-backup-\d{8}-\d{4}\.db", r.headers["Content-Disposition"])
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='backup.download'").fetchone()[0] == 1

def test_list_orgs_counts_and_audit(client, make_org, make_user, login_as, api, db):
    org = make_org("A")
    make_user("hong", org_id=org); make_user("root", superadmin=True)
    db.execute("INSERT INTO events (org_id, name, created_at) VALUES (?,?,0)", (org, "행사"))
    login_as(client, "root")
    orgs = api(client, "GET", "/api/admin/super/orgs").json["orgs"]
    o = next(x for x in orgs if x["id"] == org)
    assert o["user_count"] == 1 and o["event_count"] == 1 and o["is_active"] is True
    r = api(client, "POST", "/api/admin/super/orgs", json={"name": "새", "slug": "new-org"})
    row = db.execute("SELECT org_id FROM audit_log WHERE action='org.create'").fetchone()
    assert row["org_id"] == r.json["org"]["id"]

def test_patch_slug_validation_and_audit(client, make_org, make_user, login_as, api, db):
    a, b = make_org("A", "aaa"), make_org("B", "bbb")
    make_user("root", superadmin=True); login_as(client, "root")
    assert api(client, "PATCH", f"/api/admin/super/orgs/{a}", json={"slug": "bbb"}).json["error"] == "slug_taken"
    assert api(client, "PATCH", f"/api/admin/super/orgs/{a}", json={"slug": "Bad_Slug"}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/super/orgs/{a}", json={"is_active": "no"}).status_code == 422
    assert api(client, "PATCH", "/api/admin/super/orgs/9999", json={"name": "x"}).status_code == 404
    r = api(client, "PATCH", f"/api/admin/super/orgs/{a}", json={"slug": "ccc", "is_active": False})
    assert r.json["org"]["slug"] == "ccc" and r.json["org"]["is_active"] is False
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='org.update'").fetchone()[0] == 1

def test_slug_rejects_trailing_newline(client, make_org, make_user, login_as, api):
    org = make_org("A", "aaa")
    make_user("root", superadmin=True); login_as(client, "root")
    assert api(client, "POST", "/api/admin/super/orgs", json={"name": "x", "slug": "abc\n"}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/super/orgs/{org}", json={"slug": "abc\n"}).status_code == 422

def test_backup_contains_data_and_forbidden_for_owner(app, client, make_org, make_user, login_as, api, tmp_path):
    import sqlite3
    make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "GET", "/api/admin/super/backup")
    f = tmp_path / "b.db"; f.write_bytes(r.data)
    c = sqlite3.connect(str(f))
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='root'").fetchone()[0] == 1
    c.close()
    other = app.test_client()
    make_user("hong", org_id=make_org()); login_as(other, "hong")
    assert api(other, "GET", "/api/admin/super/backup").status_code == 403


def test_whoami_ip(client, make_user, login_as, api):
    make_user("root", superadmin=True); login_as(client, "root")
    r = client.get("/api/admin/super/whoami-ip", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200
    assert set(r.json) == {"remote_addr", "x_forwarded_for"} and r.json["x_forwarded_for"] == "1.2.3.4"
