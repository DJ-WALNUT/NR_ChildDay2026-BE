"""라우트 블루프린트."""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import admin_users, auth

    app.register_blueprint(auth.bp)
    app.register_blueprint(admin_users.bp)
