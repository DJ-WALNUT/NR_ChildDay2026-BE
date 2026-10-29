"""오래된 보조 행 정리: 속도 제한 기록, 만료·유휴 세션."""

from __future__ import annotations

from .auth import IDLE_SEC
from .db import tx
from .timeutil import now

_RATE_KEEP_SEC = 24 * 3600


def prune() -> int:
    """지운 행 수. 멱등하다."""
    t = now()
    with tx() as db:
        n = db.execute("DELETE FROM rate_events WHERE at < ?", (t - _RATE_KEEP_SEC,)).rowcount
        n += db.execute("DELETE FROM sessions WHERE expires_at <= ? OR last_seen_at < ?",
                        (t, t - IDLE_SEC)).rowcount
    return n
