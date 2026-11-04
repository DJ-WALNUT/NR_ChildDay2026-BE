import runpy
from pathlib import Path

import pytest

CONF = str(Path(__file__).resolve().parent.parent / "gunicorn.conf.py")


@pytest.mark.parametrize("value, bind", [(None, "0.0.0.0:5005"), ("", "0.0.0.0:5005"), ("5100", "0.0.0.0:5100")])
def test_bind_follows_app_port(monkeypatch, value, bind):
    monkeypatch.delenv("APP_PORT", raising=False)
    if value is not None:
        monkeypatch.setenv("APP_PORT", value)
    assert runpy.run_path(CONF)["bind"] == bind
