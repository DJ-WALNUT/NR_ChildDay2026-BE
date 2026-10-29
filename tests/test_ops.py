def test_cors_allows_frontend_with_credentials(client):
    r = client.options("/api/public/orgs/x", headers={
        "Origin": "https://nrbooth.team-cluster.kr", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert r.headers["Access-Control-Allow-Origin"] == "https://nrbooth.team-cluster.kr"
    assert r.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_rejects_unknown_origin(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_security_headers(client):
    h = client.get("/api/health").headers
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store" and h["Referrer-Policy"] == "no-referrer"
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Robots-Tag"] == "noindex, nofollow"


def test_cors_headers_on_401(client):
    r = client.get("/api/admin/auth/me", headers={"Origin": "https://nrbooth.team-cluster.kr"})
    assert r.status_code == 401
    assert r.headers["Access-Control-Allow-Origin"] == "https://nrbooth.team-cluster.kr"


def test_prune(app, db, make_org, make_user):
    import time

    from app.housekeeping import prune
    now = time.time()
    uid = make_user("hong", org_id=make_org())
    db.execute("INSERT INTO rate_events (key, at) VALUES ('old', 0)")
    db.execute("INSERT INTO rate_events (key, at) VALUES ('fresh', ?)", (now,))
    for h, last, exp in (("live", now, now + 3600), ("expired", now, now - 10), ("idle", 0, now + 3600)):
        db.execute("INSERT INTO sessions (id_hash, user_id, csrf, created_at, last_seen_at, expires_at)"
                   " VALUES (?,?,?,?,?,?)", (h, uid, "c", now, last, exp))
    with app.app_context():
        assert prune() == 3
    assert [r[0] for r in db.execute("SELECT key FROM rate_events")] == ["fresh"]
    assert [r[0] for r in db.execute("SELECT id_hash FROM sessions")] == ["live"]
