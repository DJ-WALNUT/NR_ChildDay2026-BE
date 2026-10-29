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


def test_prune(app, db):
    from app.housekeeping import prune
    db.execute("INSERT INTO rate_events (key, at) VALUES ('old', 0)")
    with app.app_context():
        assert prune() >= 1
    assert db.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0] == 0
