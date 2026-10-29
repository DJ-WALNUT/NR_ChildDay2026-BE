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
    with app.app_context():
        yield get_db()
