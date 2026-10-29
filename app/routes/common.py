"""라우트 공용 도구."""

from __future__ import annotations

from flask import request


def json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}
