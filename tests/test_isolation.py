"""기관 격리 매트릭스: 다른 기관 자원은 모든 /api/admin 엔드포인트(auth·super 제외)에서 404."""

import pytest

from app.db import get_db

# endpoint 이름 -> (메서드, B 의 자원으로 채운 URL, 본문 만드는 함수 또는 None)
CASES = {
    "admin_booths.get_booth": ("GET", lambda i: f"/api/admin/booths/{i['booth']}", None),
    "admin_booths.patch_booth": ("PATCH", lambda i: f"/api/admin/booths/{i['booth']}", lambda i: {"name": "x"}),
    "admin_booths.delete_booth": ("DELETE", lambda i: f"/api/admin/booths/{i['booth']}", None),
    "admin_booths.booth_reservations": ("GET", lambda i: f"/api/admin/booths/{i['booth']}/reservations", None),
    "admin_booths.clear_booth_reservations": (
        "DELETE", lambda i: f"/api/admin/booths/{i['booth']}/reservations", None),
    "admin_booths.rotate_token": ("POST", lambda i: f"/api/admin/booths/{i['booth']}/rotate-token", None),
    "admin_booths.toggle_booth": ("POST", lambda i: f"/api/admin/booths/{i['booth']}/toggle", None),
    "admin_booths.batch_status": (
        "POST", lambda i: "/api/admin/booths/batch-status",
        lambda i: {"booth_ids": [i["booth"]], "is_active": False}),
    "admin_events.update_event": (
        "PATCH", lambda i: f"/api/admin/events/{i['event']}", lambda i: {"name": "x"}),
    "admin_events.delete_event": ("DELETE", lambda i: f"/api/admin/events/{i['event']}", None),
    "admin_booths.list_booths": ("GET", lambda i: f"/api/admin/events/{i['event']}/booths", None),
    "admin_booths.create_booth": (
        "POST", lambda i: f"/api/admin/events/{i['event']}/booths", lambda i: {"name": "x"}),
    "admin_booths.bulk_create": (
        "POST", lambda i: f"/api/admin/events/{i['event']}/booths/bulk", lambda i: {"names": ["x"]}),
    "admin_events.list_partners": ("GET", lambda i: f"/api/admin/events/{i['event']}/partners", None),
    "admin_events.create_partner": (
        "POST", lambda i: f"/api/admin/events/{i['event']}/partners", lambda i: {"name": "x"}),
    "admin_booths.export_route": (
        "POST", lambda i: "/api/admin/export", lambda i: {"booth_ids": [i["booth"]]}),
    "admin_booths.org_audit": ("GET", lambda i: f"/api/admin/orgs/{i['org']}/audit", None),
    "admin_events.list_events": ("GET", lambda i: f"/api/admin/orgs/{i['org']}/events", None),
    "admin_events.create_event": (
        "POST", lambda i: f"/api/admin/orgs/{i['org']}/events", lambda i: {"name": "x"}),
    "admin_events.link_targets": ("GET", lambda i: f"/api/admin/orgs/{i['org']}/link-targets", None),
    "admin_booths.overview": ("GET", lambda i: f"/api/admin/orgs/{i['org']}/overview", None),
    "admin_users.list_users": ("GET", lambda i: f"/api/admin/orgs/{i['org']}/users", None),
    "admin_users.create_user": (
        "POST", lambda i: f"/api/admin/orgs/{i['org']}/users",
        lambda i: {"username": "newbie", "display_name": "새", "password": "password1"}),
    "admin_events.update_partner": (
        "PATCH", lambda i: f"/api/admin/partners/{i['partner']}", lambda i: {"name": "x"}),
    "admin_events.delete_partner": ("DELETE", lambda i: f"/api/admin/partners/{i['partner']}", None),
    "admin_events.rotate_partner_token": (
        "POST", lambda i: f"/api/admin/partners/{i['partner']}/rotate-token", None),
    "admin_booths.patch_reservation": (
        "PATCH", lambda i: f"/api/admin/reservations/{i['reservation']}", lambda i: {"status": "noshow"}),
    "admin_booths.remove_reservation": ("DELETE", lambda i: f"/api/admin/reservations/{i['reservation']}", None),
    "admin_booths.stats_route": ("POST", lambda i: "/api/admin/stats", lambda i: {"booth_ids": [i["booth"]]}),
    "admin_users.update_user": (
        "PATCH", lambda i: f"/api/admin/users/{i['user_b2']}", lambda i: {"display_name": "x"}),
    "admin_users.reset_password": (
        "POST", lambda i: f"/api/admin/users/{i['user_b2']}/reset-password", lambda i: {}),
}


@pytest.fixture
def world(app, make_org, make_user, make_event, make_booth, make_reservation, login_as, client):
    org_a, org_b = make_org("A", "org-a"), make_org("B", "org-b")
    make_user("hong", org_id=org_a)
    make_user("bowner", org_id=org_b)
    user_b2 = make_user("bstaff", org_id=org_b)
    event = make_event(org_b)
    with app.app_context():
        partner = get_db().execute(
            "INSERT INTO partners (event_id, name, access_token, created_at) VALUES (?,?,?,0)",
            (event, "단체", "tok-b")).lastrowid
    booth = make_booth(event, partner_id=partner)
    reservation = make_reservation(booth, "홍길동")
    login_as(client, "hong")
    return {"org": org_b, "event": event, "partner": partner, "booth": booth,
            "reservation": reservation, "user_b2": user_b2}


@pytest.mark.parametrize("name", sorted(CASES))
def test_other_org_is_404(app, client, api, world, name):
    method, url, body = CASES[name]
    path = url(world)
    endpoint, _ = app.url_map.bind("localhost").match(path, method=method)
    assert endpoint == name  # 404 가 "라우팅 실패"가 아니라 "범위 밖"임을 보장
    r = api(client, method, path, json=body(world) if body else None)
    assert r.status_code == 404, (name, r.status_code, r.get_data(as_text=True))


def test_cases_cover_every_admin_endpoint(app):
    found = {r.endpoint for r in app.url_map.iter_rules()
             if r.rule.startswith("/api/admin/") and not r.rule.startswith(("/api/admin/auth/", "/api/admin/super/"))}
    assert found == set(CASES)


def test_every_super_endpoint_forbids_owner(app, client, api, make_org, make_user, login_as):
    make_user("hong", org_id=make_org())
    login_as(client, "hong")
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/api/admin/super/")]
    assert rules
    for rule in rules:
        url = rule.rule.replace("<int:org_id>", "1")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            endpoint, _ = app.url_map.bind("localhost").match(url, method=method)
            assert endpoint == rule.endpoint
            r = api(client, method, url, json={} if method != "GET" else None)
            assert r.status_code == 403, (rule.endpoint, method, r.status_code)
