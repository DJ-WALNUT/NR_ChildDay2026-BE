import pytest

from app import create_app
from app.db import get_db


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path / "data"),
        "PREFERRED_URL_SCHEME": "https",  # 테스트 클라이언트가 https://localhost 로 요청 → Secure 쿠키 동작
        "CLEANUP_INTERVAL": 0,
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """독립 autocommit 연결. 앱 컨텍스트를 잡지 않아 요청 간 flask.g 가 새지 않는다."""
    from pathlib import Path

    from app.db import _connect

    conn = _connect(Path(app.config["DATA_DIR"]) / "app.db")
    yield conn
    conn.close()


# ---- 팩토리 (헬퍼 callable 을 돌려주는 fixture) ----

@pytest.fixture
def make_org(app):
    from app.timeutil import now

    counter = [0]

    def _make(name="기관", slug=None, active=True) -> int:
        counter[0] += 1
        with app.app_context():
            cur = get_db().execute(
                "INSERT INTO organizations (name, slug, is_active, created_at) VALUES (?,?,?,?)",
                (name, slug or f"org-{counter[0]}", int(active), now()),
            )
            return cur.lastrowid

    return _make


@pytest.fixture
def make_user(app):
    from app.security import hash_password, normalize_username
    from app.timeutil import now

    def _make(username="hong", password="password1", org_id=None, superadmin=False,
              must_change=False, active=True) -> int:
        username = normalize_username(username)
        with app.app_context():
            cur = get_db().execute(
                "INSERT INTO users (org_id, username, display_name, password_hash, must_change_password,"
                " is_superadmin, is_active, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (org_id, username, username, hash_password(password), int(must_change),
                 int(superadmin), int(active), now()),
            )
            return cur.lastrowid

    return _make


@pytest.fixture
def login_as():
    def _login(client, username, password="password1") -> str:
        r = client.post("/api/admin/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.get_data(as_text=True)
        client.csrf = r.json["csrf"]
        return client.csrf

    return _login


@pytest.fixture
def api():
    def _api(client, method, url, json=None):
        headers = {}
        csrf = getattr(client, "csrf", None)
        if csrf:
            headers["X-CSRF-Token"] = csrf
        return client.open(url, method=method, json=json, headers=headers)

    return _api
