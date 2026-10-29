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
