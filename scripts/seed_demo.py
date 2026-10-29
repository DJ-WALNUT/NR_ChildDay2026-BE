"""개발용 시드: 화면·동작 확인에 쓰는 가짜 데이터를 빈 DB 에 만든다. 운영 DB 에 쓰지 않는다.

사용: python scripts/seed_demo.py --out <DATA_DIR>/app.db
"""
import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.assets import store  # noqa: E402
from app.db import _connect, migrate  # noqa: E402
from app.security import link_token  # noqa: E402

LONG_EVENT = "2026년 광명시 청소년의 달 기념행사 오월의 난장 그리고 함께하는 어린이날 큰잔치 한마당"
LONG_BOOTH = "광명시 청소년 진로 탐색 체험 부스 나만의 직업 알아보기 그리고 미래 설계하기"
NOTICE = "비가 오면 실내 체육관에서 진행해요. 접수는 행사 당일 현장에서도 가능해요."


def demo_png() -> bytes:
    """32x32 단색 PNG (표준 라이브러리만 사용)."""
    w = h = 32
    raw = b"".join(b"\x00" + bytes((0x1F, 0x6F, 0x4A)) * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


NAMES = ["김하늘", "이서준", "박지우", "최민서", "정도윤", "강서연", "조예준", "윤하린", "장시우", "임수아",
         "한지호", "오나은", "서준혁", "신유나", "권태양", "황보라", "안재이", "송다온", "류지안", "문채원",
         "배서진", "백이든", "노아린", "남궁민", "표세아", "하루", "구름", "마루", "나래", "가온"]
AGES = ["0~8세", "9~13세", "14~16세", "17~19세", "20~24세", "24세 이상"]


def seed(db_path: Path) -> dict:
    db_path = Path(db_path)
    if db_path.exists():
        raise SystemExit(f"{db_path} 가 이미 있어요. 빈 경로에만 만들어요.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrate(db_path)
    app = create_app({"DATA_DIR": str(db_path.parent), "CLEANUP_INTERVAL": 0})
    with app.app_context():
        logo = store(demo_png())
    conn = _connect(db_path)
    now = time.time()
    try:
        def ins(sql, args):
            return conn.execute(sql, args).lastrowid

        org = lambda n, s, lg: ins(
            "INSERT INTO organizations (name, slug, is_active, created_at, logo) VALUES (?,?,1,?,?)",
            (n, s, now, lg))
        nareum = org("나름청소년활동센터", "nareum", None)
        gm = org("광명시청소년재단", "gmyouth", logo)
        ev_a = ins("INSERT INTO events (org_id, name, created_at, notice) VALUES (?,?,?,?)",
                   (nareum, "어린이날 나름 놀이터", now, NOTICE))
        ev_b = ins("INSERT INTO events (org_id, name, created_at, notice) VALUES (?,?,?,NULL)",
                   (gm, LONG_EVENT, now))
        partner = ins("INSERT INTO partners (event_id, name, access_token, created_at) VALUES (?,?,?,?)",
                      (ev_a, "광명시청소년상담복지센터", link_token(), now))

        def booth(ev, name, mode, partner_id=None, **kw):
            row = dict(use_waitlist=0, total_limit=0, start_hour=11, end_hour=13, slots_per_hour=3,
                       limit_per_slot=0, is_active=1)
            row.update(kw)
            return ins(
                "INSERT INTO booths (event_id, partner_id, name, mode, use_waitlist, total_limit, start_hour,"
                " end_hour, slots_per_hour, limit_per_slot, is_active, manage_token, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev, partner_id, name, mode, row["use_waitlist"], row["total_limit"], row["start_hour"],
                 row["end_hour"], row["slots_per_hour"], row["limit_per_slot"], row["is_active"],
                 link_token(), now))

        b_time = booth(ev_a, "페이스페인팅", "time", partner, limit_per_slot=3)
        b_fcfs = booth(ev_a, "솜사탕 만들기", "fcfs", partner, total_limit=10)
        b_wait = booth(ev_a, "대기 접수 룰렛", "fcfs", total_limit=3, use_waitlist=1)
        b_full = booth(ev_a, "마감된 슈팅 게임", "fcfs", total_limit=2)
        booth(ev_a, "쉬는 중인 부스", "fcfs", is_active=0)
        b_long = booth(ev_b, LONG_BOOTH, "fcfs", total_limit=20)

        plan = [  # (booth, 건수, 슬롯 목록, 상태 목록)
            (b_time, 12, ["11시 A타임", "11시 B타임", "11시 C타임", "12시 A타임"],
             ["normal"] * 8 + ["completed"] * 2 + ["noshow"] * 2),
            (b_fcfs, 8, ["선착순"], ["normal"] * 5 + ["completed", "noshow", "completed"]),
            (b_wait, 5, ["선착순"], ["normal"] * 3 + ["waiting"] * 2),
            (b_full, 2, ["선착순"], ["normal", "completed"]),
            (b_long, 3, ["선착순"], ["normal", "normal", "completed"]),
        ]
        i = 0
        for bid, n, slots, statuses in plan:
            for k in range(n):
                ins("INSERT INTO reservations (booth_id, name, gender, age_group, code, slot, status, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (bid, NAMES[i], "남" if i % 2 else "여", AGES[i % 6], f"{1000 + i * 7:04d}",
                     slots[k % len(slots)], statuses[k], now - 3600 + i * 60))
                i += 1
        total = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        tokens = conn.execute("SELECT id, name, manage_token FROM booths ORDER BY id").fetchall()
        ptoken = conn.execute("SELECT access_token FROM partners").fetchone()[0]
        out = {"reservations": total, "booths": len(tokens), "ids": {"time": b_time, "fcfs": b_fcfs,
               "wait": b_wait, "full": b_full, "long": b_long}, "booth_tokens": {t["id"]: t["manage_token"] for t in tokens},
               "partner_token": ptoken}
    finally:
        conn.close()

    print("방문객 주소: /nareum  /gmyouth")
    print(f"부스 링크 1 (시간제): /b/{out['booth_tokens'][b_time]}")
    print(f"부스 링크 2 (선착순): /b/{out['booth_tokens'][b_fcfs]}")
    print(f"운영 단체 링크: /p/{ptoken}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    seed(Path(ap.parse_args(argv).out))


if __name__ == "__main__":
    main()
