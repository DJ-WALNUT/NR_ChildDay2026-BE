FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DB 는 볼륨에 둔다. flask CLI(create-superadmin)가 앱을 찾도록 FLASK_APP 도 지정한다.
ENV DATA_DIR=/data \
    FLASK_APP=wsgi.py
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 5005

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
