"""요청 행위자(Principal). 계정 또는 링크."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flask import g


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "booth_link", "partner_link"]
    label: str
    user_id: int | None = None
    org_id: int | None = None  # 계정의 기관
    is_superadmin: bool = False
    booth_id: int | None = None
    partner_id: int | None = None


def set_principal(p: Principal | None) -> None:
    g.principal = p


def current_principal() -> Principal | None:
    return g.get("principal")
