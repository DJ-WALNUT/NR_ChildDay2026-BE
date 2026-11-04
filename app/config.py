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


def _positive(name: str, default: int) -> int:
    """제한 값용. 0 이하는 모든 요청을 막게 되므로 기본값으로 둔다."""
    value = _int(name, default)
    return value if value > 0 else default


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
    # 방문객 제한. IP+부스 단위(잘못된 링크만 IP 단위). LIMIT 은 횟수, WINDOW 는 초.
    VISITOR_RESERVE_LIMIT = _positive("VISITOR_RESERVE_LIMIT", 30)
    VISITOR_RESERVE_WINDOW = _positive("VISITOR_RESERVE_WINDOW", 60)
    # 같은 부스·같은 이름의 신청 시도. IP 와 상관없이 세어, 신청 응답으로 식별번호를 맞혀 보는 속도를 묶는다.
    VISITOR_RESERVE_NAME_LIMIT = _positive("VISITOR_RESERVE_NAME_LIMIT", 20)
    VISITOR_RESERVE_NAME_WINDOW = _positive("VISITOR_RESERVE_NAME_WINDOW", 900)
    VISITOR_LOOKUP_LIMIT = _positive("VISITOR_LOOKUP_LIMIT", 20)
    VISITOR_LOOKUP_WINDOW = _positive("VISITOR_LOOKUP_WINDOW", 900)
    # 위 조회 제한을 부스 구분 없이 합친 IP 전체 상한. 기본은 사실상 제한이 없는 값이다.
    VISITOR_LOOKUP_TOTAL_LIMIT = _positive("VISITOR_LOOKUP_TOTAL_LIMIT", 10000)
    VISITOR_LOOKUP_TOTAL_WINDOW = _positive("VISITOR_LOOKUP_TOTAL_WINDOW", 900)
    LINK_MISS_LIMIT = _positive("LINK_MISS_LIMIT", 30)
    LINK_MISS_WINDOW = _positive("LINK_MISS_WINDOW", 900)
    TESTING = False
