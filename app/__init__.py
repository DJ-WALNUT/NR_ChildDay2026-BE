"""부스 예약 서버 앱 팩토리."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .db import close_db, migrate
from .auth import init_auth
from .cli import init_cli
from .errors import ApiError
from .routes import register_blueprints


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

    origins = [o.strip() for o in str(app.config["CORS_ORIGINS"]).split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": origins}}, supports_credentials=True,
         allow_headers=["Content-Type", "X-CSRF-Token"], methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])

    app.teardown_appcontext(close_db)
    init_auth(app)
    init_cli(app)
    register_blueprints(app)

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

    @app.after_request
    def _security_headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        if _is_api(request.path):
            h.setdefault("X-Robots-Tag", "noindex, nofollow")
            h.setdefault("Cache-Control", "no-store")
        return resp

    if app.config["CLEANUP_INTERVAL"] > 0 and not app.testing:
        _start_cleanup(app)
    return app


def _start_cleanup(app: Flask) -> None:
    """오래된 보조 행을 주기적으로 지우는 데몬 스레드."""
    from .housekeeping import prune

    interval = app.config["CLEANUP_INTERVAL"]

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                with app.app_context():
                    prune()
            except Exception:  # 정리 실패로 서버가 죽으면 안 된다
                app.logger.exception("정리 실패")

    threading.Thread(target=loop, name="cleanup", daemon=True).start()
