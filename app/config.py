"""환경변수 설정."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


class Config:
    DATA_DIR = os.getenv("DATA_DIR") or str(_ROOT / "data")
    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,https://nrbooth.team-cluster.kr,https://2026child.team-cluster.kr",
    )
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1") != "0"
    # Cloudflare(1) + 리버스 프록시(1) = 2. 프록시 없이 직접 붙이면 0.
    TRUSTED_PROXY_HOPS = _int("TRUSTED_PROXY_HOPS", 2)
    CLEANUP_INTERVAL = _int("CLEANUP_INTERVAL", 600)  # 초
    TESTING = False
