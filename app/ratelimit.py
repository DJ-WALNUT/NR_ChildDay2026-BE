"""SQLite 기반 속도 제한. 키별 요청 시각을 rate_events 에 쌓는다."""

from __future__ import annotations

from .db import get_db
from .errors import ApiError
from .timeutil import now

_KEEP_SEC = 24 * 3600  # hit 때 이보다 오래된 행은 지운다


def hit(key: str) -> None:
    db = get_db()
    t = now()
    db.execute("DELETE FROM rate_events WHERE key = ? AND at < ?", (key, t - _KEEP_SEC))
    db.execute("INSERT INTO rate_events (key, at) VALUES (?, ?)", (key, t))


def exceeded(key: str, limit: int, window_sec: int) -> bool:
    row = get_db().execute(
        "SELECT COUNT(*) FROM rate_events WHERE key = ? AND at > ?",
        (key, now() - window_sec),
    ).fetchone()
    return row[0] >= limit


def clear(key: str) -> None:
    get_db().execute("DELETE FROM rate_events WHERE key = ?", (key,))


def clear_prefix(prefix: str) -> None:
    """prefix 로 시작하는 키를 모두 지운다 (LIKE 를 쓰지 않아 % · _ 가 든 이메일도 안전)."""
    get_db().execute("DELETE FROM rate_events WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))


def guard(key: str, limit: int, window_sec: int) -> None:
    """한도를 넘었으면 429, 아니면 이번 요청을 기록한다."""
    if exceeded(key, limit, window_sec):
        raise ApiError(429, "rate_limited")
    hit(key)
