def test_issue_account_and_first_login(client, app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "Kim", "display_name": "김담당"})
    assert r.status_code == 201 and r.json["user"]["username"] == "kim"
    c2 = app.test_client()
    me = c2.post("/api/admin/auth/login", json={"username": "kim", "password": r.json["temp_password"]}).json
    assert me["user"]["must_change_password"] is True


def test_duplicate_username_case_insensitive(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "HONG", "display_name": "x"})
    assert r.status_code == 409 and r.json["error"] == "username_taken"


def test_issue_validation(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    url = f"/api/admin/orgs/{org}/users"
    r = api(client, "POST", url, json={"username": "a b", "display_name": "x"})
    assert r.status_code == 422 and "username" in r.json["fields"]
    r = api(client, "POST", url, json={"username": "valid1", "display_name": " "})
    assert r.status_code == 422 and "display_name" in r.json["fields"]
    r = api(client, "POST", url, json={"username": "valid1", "display_name": "x" * 41})
    assert r.status_code == 422 and "display_name" in r.json["fields"]


def test_list_users_and_audit(client, db, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "kim", "display_name": "김담당"})
    r = api(client, "GET", f"/api/admin/orgs/{org}/users")
    assert [u["username"] for u in r.json["users"]] == ["hong", "kim"]
    assert set(r.json["users"][0]) == {"id", "username", "display_name", "is_active",
                                       "must_change_password", "last_login_at", "created_at"}
    row = db.execute("SELECT * FROM audit_log WHERE action = 'user.create'").fetchone()
    assert row["org_id"] == org and row["summary"] == "계정 발급: 김담당(kim)"


def test_reset_password_revokes_and_forces_change(app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); kim = make_user("kim", org_id=org)
    ck, ch = app.test_client(), app.test_client()
    login_as(ck, "kim"); login_as(ch, "hong")
    temp = api(ch, "POST", f"/api/admin/users/{kim}/reset-password").json["temp_password"]
    assert ck.get("/api/admin/auth/me").status_code == 401
    me = app.test_client().post("/api/admin/auth/login", json={"username": "kim", "password": temp}).json
    assert me["user"]["must_change_password"] is True


def test_reset_password_self_rejected(client, make_org, make_user, login_as, api):
    org = make_org(); me = make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "POST", f"/api/admin/users/{me}/reset-password")
    assert r.status_code == 409 and r.json["error"] == "use_password_change"


def test_last_active_and_self_guard(client, make_org, make_user, login_as, api):
    org = make_org(); me = make_user("hong", org_id=org); other = make_user("kim", org_id=org)
    login_as(client, "hong")
    assert api(client, "PATCH", f"/api/admin/users/{me}", json={"is_active": False}).json["error"] == "cannot_deactivate_self"
    assert api(client, "PATCH", f"/api/admin/users/{other}", json={"is_active": False}).status_code == 200
    # 이제 hong이 유일한 활성 계정: 슈퍼관리자가 비활성화하려 해도 막힌다
    make_user("root", superadmin=True); login_as(client, "root")
    assert api(client, "PATCH", f"/api/admin/users/{me}", json={"is_active": False}).json["error"] == "last_active_user"


def test_deactivate_revokes_sessions_and_reactivate(app, db, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); kim = make_user("kim", org_id=org)
    ck, ch = app.test_client(), app.test_client()
    login_as(ck, "kim"); login_as(ch, "hong")
    r = api(ch, "PATCH", f"/api/admin/users/{kim}", json={"is_active": False, "display_name": "새이름"})
    assert r.status_code == 200 and r.json["user"]["is_active"] is False
    assert r.json["user"]["display_name"] == "새이름"
    assert ck.get("/api/admin/auth/me").status_code == 401
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (kim,)).fetchone()[0] == 0
    r = api(ch, "PATCH", f"/api/admin/users/{kim}", json={"is_active": True})
    assert r.json["user"]["is_active"] is True
    actions = [x["action"] for x in db.execute("SELECT action FROM audit_log WHERE action LIKE 'user.%'")]
    assert "user.deactivate" in actions and "user.activate" in actions and "user.update" in actions


def test_patch_validation(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); kim = make_user("kim", org_id=org)
    login_as(client, "hong")
    assert api(client, "PATCH", f"/api/admin/users/{kim}", json={"display_name": ""}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/users/{kim}", json={"is_active": "no"}).status_code == 422


def test_cross_org_and_superadmin_targets_hidden(client, make_org, make_user, login_as, api):
    a, b = make_org(), make_org()
    make_user("hong", org_id=a); other = make_user("kim", org_id=b); root = make_user("root", superadmin=True)
    login_as(client, "hong")
    assert api(client, "GET", f"/api/admin/orgs/{b}/users").status_code == 404
    assert api(client, "POST", f"/api/admin/users/{other}/reset-password").status_code == 404
    assert api(client, "POST", f"/api/admin/users/{root}/reset-password").status_code == 404
    assert api(client, "PATCH", f"/api/admin/users/{other}", json={"display_name": "x"}).status_code == 404


def test_superadmin_manages_any_org(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "first", "display_name": "첫 담당"})
    assert r.status_code == 201
    assert api(client, "GET", "/api/admin/orgs/9999/users").status_code == 404
