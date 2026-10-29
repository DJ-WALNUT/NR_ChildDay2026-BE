"""시간 도구. 저장은 UNIX 초(float), 날짜 판단은 KST(UTC+9)."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now() -> float:
    return time.time()


def kst_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, KST).date()
