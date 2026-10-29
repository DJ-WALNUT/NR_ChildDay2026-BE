"""로고 이미지 자산: 내용 해시 이름으로 DATA_DIR/assets 에 저장한다."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from flask import current_app

from .db import get_db
from .errors import ApiError, ValidationError

MAX_BYTES = 1_048_576
NAME_RE = re.compile(r"[0-9a-f]{64}\.(png|jpg|webp)")
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _dir() -> Path:
    return Path(current_app.config["DATA_DIR"]) / "assets"


def store(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ApiError(413, "file_too_large")
    ext = sniff(data)
    if ext is None:
        raise ValidationError({"file": "PNG, JPG, WebP 이미지만 올릴 수 있어요."})
    name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / name
    # 이미 있어도 항상 다시 쓴다(동일 내용): 동시 release 가 파일을 지워도 store 후엔 존재하도록.
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return name


def url(name: str | None) -> str | None:
    return f"/api/public/assets/{name}" if name else None


def path_for(name: str) -> Path | None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None
    p = _dir() / name
    return p if p.is_file() else None


def release(name: str | None) -> None:
    """어디에서도 참조하지 않으면 파일 삭제. 트랜잭션 커밋 뒤에 호출한다."""
    if not name or not NAME_RE.fullmatch(name):
        return
    row = get_db().execute(
        "SELECT (SELECT COUNT(*) FROM organizations WHERE logo=?)"
        " + (SELECT COUNT(*) FROM events WHERE logo=?) AS n",
        (name, name),
    ).fetchone()
    if row["n"]:
        return
    p = path_for(name)
    if p:
        try:
            p.unlink()
        except OSError:
            pass
