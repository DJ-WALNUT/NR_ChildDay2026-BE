"""부스 예약 서버 앱 팩토리."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import close_db, migrate
from .errors import ApiError


def _is_api(path: str) -> bool:
    return path.startswith("/api/")


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.update(overrides or {})  # overrides 가 환경변수보다 우선

    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    version = migrate(data_dir / "app.db")
    if not app.testing:
        app.logger.setLevel(logging.INFO)
        app.logger.info("DB 위치: %s / 스키마 버전: %s", data_dir / "app.db", version)

    hops = int(app.config["TRUSTED_PROXY_HOPS"])
    if hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    app.teardown_appcontext(close_db)

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify(e.body()), e.status, e.headers

    def _json_error(code: str, status: int):
        def handler(e):
            if _is_api(request.path):
                return jsonify(error=code), status
            return e

        return handler

    app.register_error_handler(404, _json_error("not_found", 404))
    app.register_error_handler(405, _json_error("method_not_allowed", 405))
    return app
