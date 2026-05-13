FROM python:3.11-slim
WORKDIR /app

# 1. gunicorn 패키지를 설치 목록에 추가합니다.
COPY . .
RUN pip install --no-cache-dir flask flask-sqlalchemy flask-cors flask-socketio gunicorn

# 2. Flask 내장 서버 대신 Gunicorn으로 5005번 포트를 열도록 실행 명령어를 변경합니다.
# DS220+의 2코어 CPU에 맞게 워커(workers)는 4개로 설정합니다.
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:5005", "app:app"]