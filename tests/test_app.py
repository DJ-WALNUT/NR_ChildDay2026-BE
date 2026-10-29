def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json == {"ok": True}

def test_schema_tables(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"organizations", "users", "sessions", "events", "partners", "booths",
            "reservations", "audit_log", "rate_events", "schema_version"} <= names

def test_unknown_api_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json == {"error": "not_found"}

def test_validation_error_shape(app):
    from app.errors import ValidationError
    e = ValidationError({"name": "필수"})
    assert e.status == 422 and e.body() == {"error": "invalid", "fields": {"name": "필수"}}

def test_kst_date_boundary():
    from app.timeutil import kst_date
    import datetime as dt
    ts = dt.datetime(2026, 5, 4, 15, 0, 1, tzinfo=dt.timezone.utc).timestamp()
    assert kst_date(ts) == dt.date(2026, 5, 5)
