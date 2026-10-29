"""gunicorn 설정. DS220+ 2코어에 맞춰 워커 4개."""

bind = "0.0.0.0:5005"
workers = 4
preload_app = False  # 워커마다 create_app() 실행 -> 마이그레이션은 DB 락으로 직렬화된다
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
