"""기존(단일 행사) reservations.db 를 새 멀티테넌트 스키마로 이관한다. 스펙 8절."""
import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import _connect, migrate  # noqa: E402
from app.security import link_token  # noqa: E402

HOST_EVENT = "2026년 광명시 청소년의 달 기념행사 오월의 난장"
DEFAULT_EVENT = "기본 행사(미분류)"
ORGS = [("나름청소년활동센터", "nareum"), ("광명시청소년재단", "gmyouth")]

STATUSES = ("normal", "completed", "noshow", "waiting")
GENDERS = ("남", "여")
MODES = ("time", "fcfs")
OLD_FCFS = "선착순 접수"
NEW_FCFS = "선착순"

# 필드: (최소, 최대, 기본값). 새 검증 범위(app/booth_ops.py)와 같다.
_RANGES = {
    "total_limit": (0, 10**9, 0),
    "start_hour": (0, 23, 11),
    "end_hour": (0, 23, 16),
    "slots_per_hour": (1, 6, 3),
    "limit_per_slot": (0, 10**9, 0),
}


class MigrationError(Exception):
    pass


def _ts(value, fallback):
    """'YYYY-MM-DD HH:MM:SS'(UTC) → UNIX 초. 파싱 실패·NULL 이면 fallback."""
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
    return fallback


def _booth_values(b, adjusted):
    """새 검증 범위에 맞춘 부스 설정값. 값이 바뀌면 adjusted 에 기록한다(NULL → 기본값은 제외)."""
    vals = {}
    for field, (lo, hi, default) in _RANGES.items():
        v = b[field]
        vals[field] = default if v is None else max(lo, min(hi, int(v)))
    if vals["start_hour"] > vals["end_hour"]:
        vals["start_hour"], vals["end_hour"] = vals["end_hour"], vals["start_hour"]
    for field, new in vals.items():
        old = b[field]
        if old is not None and old != new:
            adjusted.append({"id": b["id"], "field": field, "from": old, "to": new})
    return vals


def _open_legacy(legacy: Path):
    conn = sqlite3.connect(f"{legacy.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check_values(kept_booths, kept_resv):
    for label, rows, col, allowed in (
        ("booth.mode", kept_booths, "mode", MODES),
        ("reservation.gender", kept_resv, "gender", GENDERS),
    ):
        ids = [r["id"] for r in rows if r[col] not in allowed]
        if ids:
            raise MigrationError(f"{label} 값이 허용 범위를 벗어난 행: {ids}")
    ids = [r["id"] for r in kept_resv if (r["status"] or "normal") not in STATUSES]
    if ids:
        raise MigrationError(f"reservation.status 값이 허용 범위를 벗어난 행: {ids}")
    ids = [r["id"] for r in kept_resv if r["time"] is None]
    if ids:
        raise MigrationError(f"reservation.time 이 NULL 인 행: {ids}")


def _migrate(src, out: Path, now: float) -> dict:
    events = src.execute("SELECT * FROM event ORDER BY id").fetchall()
    booths = src.execute("SELECT * FROM booth ORDER BY id").fetchall()
    resv = src.execute("SELECT * FROM reservation ORDER BY id").fetchall()

    default_ids = {e["id"] for e in events if e["name"] == DEFAULT_EVENT}
    host_ids = {e["id"] for e in events if e["name"] == HOST_EVENT}
    if not default_ids:
        raise MigrationError(f"기존 DB에서 '{DEFAULT_EVENT}' 행사를 찾지 못했습니다.")
    if not host_ids:
        raise MigrationError(f"기존 DB에서 '{HOST_EVENT}' 행사를 찾지 못했습니다.")

    event_ids = {e["id"] for e in events}
    booth_ids = {b["id"] for b in booths}
    bad = [b["id"] for b in booths if b["event_id"] is not None and b["event_id"] not in event_ids]
    if bad:
        raise MigrationError(f"존재하지 않는 행사를 가리키는 부스: {bad}")
    bad = [r["id"] for r in resv if r["booth_id"] not in booth_ids]
    if bad:
        raise MigrationError(f"존재하지 않는 부스를 가리키는 신청: {bad}")

    kept_events = [e for e in events if e["id"] not in default_ids]
    kept_booths = [b for b in booths if b["event_id"] is not None and b["event_id"] not in default_ids]
    kept_booth_ids = {b["id"] for b in kept_booths}
    kept_resv = [r for r in resv if r["booth_id"] in kept_booth_ids]
    _check_values(kept_booths, kept_resv)

    report = {
        "orgs": {slug: {"events": 0, "booths": 0, "reservations": 0} for _, slug in ORGS},
        "dropped": {"events": len(default_ids), "booths": len(booths) - len(kept_booths),
                    "reservations": len(resv) - len(kept_resv)},
        "status": {s: 0 for s in STATUSES},
        "slot_renamed": 0,
        "nonstandard_codes": 0,
        "adjusted_booths": [],
    }
    migrate(out)
    conn = _connect(out)
    booth_created = {b["id"]: _ts(b["created_at"], now) for b in booths}
    try:
        conn.execute("BEGIN IMMEDIATE")
        org_ids = {}
        for name, slug in ORGS:
            org_ids[slug] = conn.execute("INSERT INTO organizations (name, slug, created_at) VALUES (?,?,?)",
                                         (name, slug, now)).lastrowid
        event_slug = {}
        for e in kept_events:
            slug = "gmyouth" if e["id"] in host_ids else "nareum"
            event_slug[e["id"]] = slug
            conn.execute("INSERT INTO events (id, org_id, name, created_at) VALUES (?,?,?,?)",
                         (e["id"], org_ids[slug], e["name"], _ts(e["created_at"], now)))
            report["orgs"][slug]["events"] += 1
        booth_slug = {}
        for b in kept_booths:
            v = _booth_values(b, report["adjusted_booths"])
            conn.execute(
                "INSERT INTO booths (id, event_id, partner_id, name, mode, use_waitlist, total_limit, start_hour,"
                " end_hour, slots_per_hour, limit_per_slot, is_active, manage_token, created_at)"
                " VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)",
                (b["id"], b["event_id"], b["name"], b["mode"], 1 if b["use_waitlist"] else 0,
                 v["total_limit"], v["start_hour"], v["end_hour"], v["slots_per_hour"], v["limit_per_slot"],
                 0 if b["is_active"] is not None and not b["is_active"] else 1,
                 link_token(), booth_created[b["id"]]))
            booth_slug[b["id"]] = event_slug[b["event_id"]]
            report["orgs"][booth_slug[b["id"]]]["booths"] += 1
        for r in kept_resv:
            slot = r["time"]
            if slot == OLD_FCFS:
                slot = NEW_FCFS
                report["slot_renamed"] += 1
            status = r["status"] or "normal"
            code = r["phone"]
            if not (isinstance(code, str) and len(code) == 4 and code.isascii() and code.isdigit()):
                report["nonstandard_codes"] += 1
            conn.execute(
                "INSERT INTO reservations (id, booth_id, name, gender, age_group, code, slot, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (r["id"], r["booth_id"], r["name"], r["gender"], r["ageGroup"], code, slot, status,
                 _ts(r["created_at"], booth_created[r["booth_id"]])))
            report["status"][status] += 1
            report["orgs"][booth_slug[r["booth_id"]]]["reservations"] += 1

        got = tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("events", "booths", "reservations"))
        expect = (len(events) - report["dropped"]["events"], len(booths) - report["dropped"]["booths"],
                  len(resv) - report["dropped"]["reservations"])
        if got != expect:
            raise MigrationError(f"건수 대조 실패: 새 DB {got} != 기대 {expect}")
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        raise MigrationError(f"제약 위반: {e}") from e
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return report


def run(legacy: Path, out: Path, *, now_ts: float | None = None) -> dict:
    legacy, out = Path(legacy), Path(out)
    if out.exists():
        raise MigrationError(f"출력 파일이 이미 있습니다: {out}")
    if not legacy.is_file():
        raise MigrationError(f"기존 DB 파일이 없습니다: {legacy}")
    now = time.time() if now_ts is None else now_ts
    out.parent.mkdir(parents=True, exist_ok=True)
    src = _open_legacy(legacy)
    try:
        return _migrate(src, out, now)
    except BaseException:
        for suffix in ("", "-wal", "-shm"):
            Path(str(out) + suffix).unlink(missing_ok=True)
        raise
    finally:
        src.close()


def format_report(rep: dict) -> str:
    lines = ["== 이관 보고서 =="]
    for slug, c in rep["orgs"].items():
        lines.append(f"[{slug}] 행사 {c['events']} / 부스 {c['booths']} / 신청 {c['reservations']}")
    d = rep["dropped"]
    lines.append(f"[버림] 행사 {d['events']} / 부스 {d['booths']} / 신청 {d['reservations']}")
    lines.append("[상태] " + ", ".join(f"{k} {v}" for k, v in rep["status"].items()))
    lines.append(f"슬롯 이름 변경('선착순 접수' -> '선착순'): {rep['slot_renamed']}")
    lines.append(f"비표준 식별번호(4자리 숫자 아님, 그대로 이관): {rep['nonstandard_codes']}")
    lines.append(f"조정된 부스 값: {len(rep['adjusted_booths'])}")
    for a in rep["adjusted_booths"]:
        lines.append(f"  부스 {a['id']} {a['field']}: {a['from']} -> {a['to']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="기존 reservations.db 를 새 스키마로 이관")
    p.add_argument("--legacy", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args(argv)
    try:
        rep = run(a.legacy, a.out)
    except MigrationError as e:
        print(f"이관 실패: {e}", file=sys.stderr)
        return 1
    print(format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
