"""SQLite 연결·마이그레이션."""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask import current_app, g

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _enable_wal(conn: sqlite3.Connection) -> None:
    """새 파일에서 WAL 전환은 배타 잠금이 필요하고 busy timeout 을 따르지 않아, 짧게 재시도한다."""
    deadline = time.monotonic() + 10
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _enable_wal(conn)
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect(Path(current_app.config["DATA_DIR"]) / "app.db")
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT. 예외가 나면 ROLLBACK."""
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _statements(script: str) -> Iterator[str]:
    """SQL 스크립트를 문장 단위로 나눈다 (executescript 는 암묵적 COMMIT 을 하므로 쓰지 않는다)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""
    if buf.strip():
        yield buf


def migrate(db_path: Path) -> int:
    """migrations/NNN_*.sql 을 순서대로 적용하고 현재 버전을 돌려준다.

    마이그레이션마다 BEGIN IMMEDIATE 로 쓰기 잠금을 잡고, 잠금 안에서 버전을 다시 읽어
    여러 워커가 동시에 시작해도 한 번만 적용되며 중간에 죽어도 버전과 테이블이 어긋나지 않는다.
    """
    conn = _connect(db_path)
    try:
        files = sorted(
            (int(m.group(1)), f)
            for f in MIGRATIONS_DIR.glob("*.sql")
            if (m := re.match(r"(\d+)_", f.name))
        )
        current = 0
        for ver, f in files:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
                current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
                if ver > current:
                    for stmt in _statements(f.read_text(encoding="utf-8")):
                        conn.execute(stmt)
                    conn.execute("DELETE FROM schema_version")
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (ver,))
                    current = ver
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return current
    finally:
        conn.close()
