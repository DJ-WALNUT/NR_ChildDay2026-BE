def test_login_me_logout(client, make_org, make_user, login_as, api):
    org = make_org(slug="nareum"); make_user("hong", org_id=org)
    login_as(client, " HONG ")
    me = client.get("/api/admin/auth/me").json
    assert me["user"]["username"] == "hong" and me["org"]["slug"] == "nareum"
    assert api(client, "POST", "/api/admin/auth/logout").status_code == 204
    assert client.get("/api/admin/auth/me").status_code == 401

def test_wrong_password_and_rate_limit(client, make_org, make_user):
    make_user("hong", org_id=make_org())
    for _ in range(10):
        assert client.post("/api/admin/auth/login", json={"username": "hong", "password": "x"}).status_code == 401
    r = client.post("/api/admin/auth/login", json={"username": "hong", "password": "password1"})
    assert r.status_code == 429

def test_inactive_user_or_org_cannot_login(client, make_org, make_user):
    make_user("a1", org_id=make_org(), active=False)
    make_user("b1", org_id=make_org(active=False))
    for u in ("a1", "b1"):
        r = client.post("/api/admin/auth/login", json={"username": u, "password": "password1"})
        assert r.status_code == 401 and r.json["error"] == "invalid_credentials"

def test_csrf_required(client, make_org, make_user, login_as):
    make_user("hong", org_id=make_org()); login_as(client, "hong")
    assert client.post("/api/admin/auth/logout").json["error"] == "csrf"

def test_must_change_password_gate(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org, must_change=True); login_as(client, "hong")
    r = api(client, "GET", f"/api/admin/orgs/{org}/users")
    assert r.status_code == 403 and r.json["error"] == "password_change_required"
    assert client.get("/api/admin/auth/me").json["user"]["must_change_password"] is True
    r = api(client, "POST", "/api/admin/auth/password", json={"current": "password1", "new": "hong"})
    assert r.status_code == 422
    assert api(client, "POST", "/api/admin/auth/password",
               json={"current": "password1", "new": "newpass99"}).status_code == 204
    assert client.get("/api/admin/auth/me").json["user"]["must_change_password"] is False

def test_password_change_revokes_other_sessions(app, make_org, make_user, login_as, api):
    make_user("hong", org_id=make_org())
    c1, c2 = app.test_client(), app.test_client()
    login_as(c1, "hong"); login_as(c2, "hong")
    api(c1, "POST", "/api/admin/auth/password", json={"current": "password1", "new": "newpass99"})
    assert c1.get("/api/admin/auth/me").status_code == 200
    assert c2.get("/api/admin/auth/me").status_code == 401

def test_deactivated_mid_session(client, db, make_org, make_user, login_as):
    uid = make_user("hong", org_id=make_org()); login_as(client, "hong")
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
    assert client.get("/api/admin/auth/me").status_code == 401

def test_cli_create_superadmin(app):
    r = app.test_cli_runner().invoke(args=["create-superadmin", "--username", "Root", "--password", "rootpass1"])
    assert r.exit_code == 0
    c = app.test_client()
    me = c.post("/api/admin/auth/login", json={"username": "root", "password": "rootpass1"}).json
    assert me["user"]["is_superadmin"] is True and me["user"]["must_change_password"] is False

def test_login_and_password_change_are_audited(app, client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    api(client, "POST", "/api/admin/auth/password", json={"current": "password1", "new": "newpass99"})
    with app.app_context():
        from app.db import get_db
        rows = get_db().execute("SELECT action, org_id, actor_label FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["org_id"]) for r in rows] == [("auth.login", org), ("auth.password_change", org)]
    assert rows[0]["actor_label"] == "hong(hong)"
