"""WSGI 진입점. gunicorn 과 flask CLI(FLASK_APP=wsgi.py)가 사용한다."""

from app import create_app

app = create_app()
