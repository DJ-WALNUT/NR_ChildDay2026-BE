"""라우트 블루프린트."""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import auth

    app.register_blueprint(auth.bp)
