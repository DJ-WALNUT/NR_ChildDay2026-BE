import io
from tests.test_assets import PNG, JPG, SVG

def up(api, client, url, data, field="file"):
    return client.open(url, method="PUT", data={field: (io.BytesIO(data), "x.png")},
                       headers={"X-CSRF-Token": client.csrf}, content_type="multipart/form-data")

def test_org_logo_roundtrip(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = up(api, client, f"/api/admin/orgs/{org}/logo", PNG)
    assert r.status_code == 200 and r.json["logo_url"].startswith("/api/public/assets/")
    g = client.get(r.json["logo_url"])
    assert g.status_code == 200 and g.data == PNG and g.headers["Content-Type"] == "image/png"
    assert g.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert g.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert api(client, "DELETE", f"/api/admin/orgs/{org}/logo").status_code == 204
    assert client.get(r.json["logo_url"]).status_code == 404

def test_shared_image_survives_one_delete(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    u = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    assert up(api, client, f"/api/admin/events/{ev}/logo", PNG).json["logo_url"] == u
    api(client, "DELETE", f"/api/admin/orgs/{org}/logo")
    assert client.get(u).status_code == 200

def test_replace_releases_old(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    old = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    up(api, client, f"/api/admin/orgs/{org}/logo", JPG)
    assert client.get(old).status_code == 404

def test_bad_uploads(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    u = f"/api/admin/orgs/{org}/logo"
    assert up(api, client, u, SVG).status_code == 422
    assert up(api, client, u, b"").status_code == 422
    assert up(api, client, u, PNG, field="other").status_code == 422
    assert api(client, "PUT", u, json={"file": "x"}).status_code == 422
    assert up(api, client, u, PNG + b"\x00" * 1_048_576).json == {"error": "file_too_large"}
    big = up(api, client, u, PNG + b"\x00" * (3 * 1024 * 1024))
    assert big.status_code == 413 and big.json == {"error": "file_too_large"}

def test_asset_name_traversal(client):
    for p in ("..%2Fapp.db", "A" * 64 + ".png", "a" * 64 + ".svg"):
        assert client.get(f"/api/public/assets/{p}").status_code == 404

def test_logo_audited_and_isolated(client, db, make_org, make_user, login_as, api, make_event):
    a, b = make_org(), make_org(); evb = make_event(b); make_user("hong", org_id=a); login_as(client, "hong")
    assert up(api, client, f"/api/admin/orgs/{b}/logo", PNG).status_code == 404
    assert up(api, client, f"/api/admin/events/{evb}/logo", PNG).status_code == 404
    up(api, client, f"/api/admin/orgs/{a}/logo", PNG)
    row = db.execute("SELECT action, org_id FROM audit_log WHERE action='org.logo'").fetchone()
    assert tuple(row) == ("org.logo", a)

def test_notice_rules(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "  시작 5분 뒤 미도착 시 취소  "})
    assert r.json["event"]["notice"] == "시작 5분 뒤 미도착 시 취소"
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "   "}).json["event"]["notice"] is None
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "가" * 501}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": 3}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={}).status_code == 422
    r = api(client, "PATCH", f"/api/admin/events/{ev}", json={"name": "새 이름"})
    assert r.json["event"]["name"] == "새 이름" and r.json["event"]["notice"] is None

def test_event_update_audit_detail_only_changed(client, db, make_org, make_user, login_as, api, make_event):
    import json
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "안내"})
    d = db.execute("SELECT detail FROM audit_log WHERE action='event.update'").fetchone()[0]
    assert json.loads(d) == {"notice": {"from": None, "to": "안내"}}

def test_event_update_noop_writes_no_audit(client, db, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    name = db.execute("SELECT name FROM events WHERE id=?", (ev,)).fetchone()[0]
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"name": name}).status_code == 200
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='event.update'").fetchone()[0] == 0

def test_event_delete_releases_logo(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    u = up(api, client, f"/api/admin/events/{ev}/logo", PNG).json["logo_url"]
    assert api(client, "DELETE", f"/api/admin/events/{ev}").status_code == 204
    assert client.get(u).status_code == 404

def test_event_delete_keeps_shared_logo(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    u = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    up(api, client, f"/api/admin/events/{ev}/logo", PNG)
    assert api(client, "DELETE", f"/api/admin/events/{ev}").status_code == 204
    assert client.get(u).status_code == 200

def test_public_payloads_carry_branding(client, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(slug="nareum"); ev = make_event(org); b = make_booth(ev)
    make_user("hong", org_id=org); login_as(client, "hong")
    org_url = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    ev_url = up(api, client, f"/api/admin/events/{ev}/logo", JPG).json["logo_url"]
    api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "안내"})
    p = client.get("/api/public/orgs/nareum").json
    assert p["org"]["logo_url"] == org_url and p["events"][0]["logo_url"] == ev_url and p["events"][0]["notice"] == "안내"
    bp = client.get(f"/api/public/orgs/nareum/booths/{b}").json
    assert (bp["org"]["logo_url"], bp["event"]["logo_url"], bp["event"]["notice"]) == (org_url, ev_url, "안내")
    assert client.get("/api/admin/auth/me").json["org"]["logo_url"] == org_url
    ev_json = api(client, "GET", f"/api/admin/orgs/{org}/events").json["events"][0]
    assert ev_json["logo_url"] == ev_url and ev_json["notice"] == "안내"

def test_payloads_null_without_branding(client, make_org, make_event, make_booth):
    org = make_org(slug="gmyouth"); b = make_booth(make_event(org))
    p = client.get("/api/public/orgs/gmyouth").json
    assert p["org"]["logo_url"] is None and p["events"][0]["logo_url"] is None and p["events"][0]["notice"] is None
