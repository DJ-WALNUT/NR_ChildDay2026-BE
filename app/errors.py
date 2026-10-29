"""API 오류 타입. 응답은 {"error": code, ...extra} JSON."""

from __future__ import annotations

from typing import NoReturn


class ApiError(Exception):
    def __init__(self, status: int, code: str, extra: dict | None = None, headers: dict | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = extra
        self.headers = headers or {}

    def body(self) -> dict:
        return {"error": self.code, **(self.extra or {})}


class ValidationError(ApiError):
    def __init__(self, fields: dict[str, str]):
        super().__init__(422, "invalid", {"fields": fields})
        self.fields = fields


def not_found() -> NoReturn:
    raise ApiError(404, "not_found")


def forbidden() -> NoReturn:
    raise ApiError(403, "forbidden")


def conflict(code: str) -> NoReturn:
    raise ApiError(409, code)
