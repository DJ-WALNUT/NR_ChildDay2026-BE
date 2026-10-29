import sqlite3

import pytest

from scripts.migrate_legacy import HOST_EVENT, MigrationError, run

SCHEMA = """
CREATE TABLE event (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, created_at DATETIME);
CREATE TABLE booth (id INTEGER PRIMARY KEY, event_id INTEGER REFERENCES event(id), name VARCHAR(100) NOT NULL,
  mode VARCHAR(20), use_waitlist BOOLEAN, total_limit INTEGER, start_hour INTEGER, end_hour INTEGER,
  slots_per_hour INTEGER, limit_per_slot INTEGER, is_active BOOLEAN, created_at DATETIME);
CREATE TABLE reservation (id INTEGER PRIMARY KEY, booth_id INTEGER NOT NULL REFERENCES booth(id),
  name VARCHAR(50) NOT NULL, gender VARCHAR(10) NOT NULL, ageGroup VARCHAR(20) NOT NULL,
  phone VARCHAR(20) NOT NULL, time VARCHAR(50), status VARCHAR(20), created_at DATETIME);
"""


def _booth(i, ev, mode="fcfs", **kw):
    d = dict(use_waitlist=None, total_limit=5, start_hour=11, end_hour=16, slots_per_hour=3,
             limit_per_slot=2, is_active=1, created_at="2026-04-25 08:32:57")
    d.update(kw)
    return (i, ev, f"부스{i}", mode, d["use_waitlist"], d["total_limit"], d["start_hour"], d["end_hour"],
            d["slots_per_hour"], d["limit_per_slot"], d["is_active"], d["created_at"])


def _res(i, booth, slot, status, code="1234", created="2026-05-23 00:00:00"):
    return (i, booth, f"이름{i}", "남", "9~13세", code, slot, status, created)


@pytest.fixture
def legacy_db(tmp_path):
    p = tmp_path / "legacy.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO event VALUES (?,?,?)", [
        (1, "기본 행사(미분류)", "2026-04-01 00:00:00"), (2, "어린이날 나름", "2026-05-01 00:00:00"),
        (3, HOST_EVENT, "2026-05-02 00:00:00")])
    c.executemany("INSERT INTO booth VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        _booth(10, 1), _booth(20, 2, "time"), _booth(30, 3), _booth(31, None)])
    c.executemany("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)", [
        _res(1, 10, "선착순 접수", "normal"),
        _res(2, 20, "11시 A타임", "normal"), _res(3, 20, "11시 A타임", "noshow"),
        _res(4, 30, "선착순 접수", None, created=None), _res(5, 30, "선착순 접수", None, code="12345"),
        _res(6, 31, "선착순 접수", "normal")])
    c.commit()
    c.close()
    return p


def test_migrates_and_maps(tmp_path, legacy_db):
    out = tmp_path / "app.db"
    rep = run(legacy_db, out, now_ts=1.0)
    assert rep["orgs"]["nareum"] == {"events": 1, "booths": 1, "reservations": 2}
    assert rep["orgs"]["gmyouth"] == {"events": 1, "booths": 1, "reservations": 2}
    assert rep["dropped"] == {"events": 1, "booths": 2, "reservations": 2}
    assert rep["slot_renamed"] == 2
    assert rep["nonstandard_codes"] == 1
    assert rep["adjusted_booths"] == []
    assert rep["status"] == {"normal": 3, "completed": 0, "noshow": 1, "waiting": 0}
    c = sqlite3.connect(out)
    assert c.execute("SELECT slot, status FROM reservations WHERE booth_id=30 ORDER BY id").fetchall()[0] == ("선착순", "normal")
    assert c.execute("SELECT id FROM booths ORDER BY id").fetchall() == [(20,), (30,)]
    assert all(len(t) == 24 for (t,) in c.execute("SELECT manage_token FROM booths"))
    assert c.execute("SELECT slug FROM organizations o JOIN events e ON e.org_id=o.id WHERE e.id=3").fetchone() == ("gmyouth",)
    assert c.execute("SELECT created_at FROM reservations WHERE id=4").fetchone() == (
        c.execute("SELECT created_at FROM booths WHERE id=30").fetchone())
    assert c.execute("SELECT created_at FROM reservations WHERE id=2").fetchone() == (1779494400.0,)
    assert c.execute("SELECT code FROM reservations WHERE id=5").fetchone() == ("12345",)


def test_clamps_and_reports_booth_values(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("INSERT INTO booth VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              _booth(36, 3, "time", slots_per_hour=10, start_hour=16, end_hour=11))
    c.execute("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)", _res(7, 36, "11시 A타임", "normal"))
    c.commit()
    c.close()
    out = tmp_path / "app.db"
    rep = run(legacy_db, out)
    n = sqlite3.connect(out)
    assert n.execute("SELECT slots_per_hour, start_hour, end_hour FROM booths WHERE id=36").fetchone() == (6, 11, 16)
    assert {(a["id"], a["field"], a["from"], a["to"]) for a in rep["adjusted_booths"]} == {
        (36, "slots_per_hour", 10, 6), (36, "start_hour", 16, 11), (36, "end_hour", 11, 16)}
    assert n.execute("SELECT COUNT(*) FROM reservations WHERE booth_id=36").fetchone() == (1,)


def test_aborts_on_check_violation_naming_rows(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("UPDATE reservation SET gender='기타' WHERE id=4")
    c.commit()
    c.close()
    with pytest.raises(MigrationError, match=r"\[4\]"):
        run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()


def test_refuses_existing_out(tmp_path, legacy_db):
    out = tmp_path / "app.db"
    out.write_bytes(b"x")
    with pytest.raises(MigrationError):
        run(legacy_db, out)
    assert out.read_bytes() == b"x"


def test_refuses_when_host_event_missing(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("UPDATE event SET name='다른 이름' WHERE id=3")
    c.commit()
    c.close()
    with pytest.raises(MigrationError):
        run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()


def test_aborts_on_duplicate_booth_name_phone_naming_rows(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)",
              (7, 30, "이름4", "남", "9~13세", "1234", "선착순 접수", "normal", None))
    c.commit()
    c.close()
    with pytest.raises(MigrationError, match=r"중복.*\[4, 7\]"):
        run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()


def test_duplicates_in_dropped_booths_are_ignored(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)",
              (7, 10, "이름1", "남", "9~13세", "1234", "선착순 접수", "normal", None))
    c.commit()
    c.close()
    assert run(legacy_db, tmp_path / "app.db")["dropped"]["reservations"] == 3


def test_reports_orphaned_reservations_for_adjusted_time_booth(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    # 11~16시, 시간당 10칸 -> 6칸으로 줄면 'G타임' 이상 슬롯은 새 목록에 없다
    c.execute("INSERT INTO booth VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              _booth(36, 3, "time", slots_per_hour=10))
    c.executemany("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)", [
        _res(7, 36, "11시 A타임", "normal"), _res(8, 36, "11시 H타임", "normal"),
        _res(9, 36, "12시 J타임", "normal")])
    c.commit()
    c.close()
    rep = run(legacy_db, tmp_path / "app.db")
    entries = [a for a in rep["adjusted_booths"] if a["id"] == 36]
    assert entries and all(a["orphaned_reservations"] == 2 for a in entries)


def test_orphan_count_ignores_fcfs_booths(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("INSERT INTO booth VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              _booth(37, 3, "fcfs", slots_per_hour=10))
    c.execute("INSERT INTO reservation VALUES (?,?,?,?,?,?,?,?,?)", _res(7, 37, "선착순 접수", "normal"))
    c.commit()
    c.close()
    rep = run(legacy_db, tmp_path / "app.db")
    assert all(a["orphaned_reservations"] == 0 for a in rep["adjusted_booths"] if a["id"] == 37)


def test_main_reports_sqlite_error_cleanly(tmp_path, capsys):
    from scripts.migrate_legacy import main
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database" * 20)
    assert main(["--legacy", str(bad), "--out", str(tmp_path / "app.db")]) == 1
    assert "이관 실패" in capsys.readouterr().err


def test_reports_nonstandard_age_groups(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("UPDATE reservation SET ageGroup='초등' WHERE id IN (4, 5)")
    c.execute("UPDATE reservation SET ageGroup='초등' WHERE id = 1")  # 버려지는 부스 -> 세지 않음
    c.commit()
    c.close()
    rep = run(legacy_db, tmp_path / "app.db")
    assert rep["nonstandard_age_groups"] == 2
    from scripts.migrate_legacy import format_report
    assert "비표준 연령대" in format_report(rep)


def test_aborts_listing_missing_required_columns(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("ALTER TABLE booth DROP COLUMN limit_per_slot")
    c.execute("ALTER TABLE reservation DROP COLUMN phone")
    c.commit()
    c.close()
    with pytest.raises(MigrationError) as e:
        run(legacy_db, tmp_path / "app.db")
    assert "limit_per_slot" in str(e.value) and "booth" in str(e.value)
    assert not (tmp_path / "app.db").exists()


def test_aborts_when_booth_event_id_column_missing(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("ALTER TABLE booth DROP COLUMN event_id")
    c.commit()
    c.close()
    with pytest.raises(MigrationError, match="event_id"):
        run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()


def test_aborts_when_every_booth_would_be_dropped(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("UPDATE booth SET event_id = NULL")
    c.commit()
    c.close()
    with pytest.raises(MigrationError, match="모든 부스"):
        run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()


def test_optional_columns_may_be_missing(tmp_path, legacy_db):
    c = sqlite3.connect(legacy_db)
    c.execute("ALTER TABLE booth DROP COLUMN use_waitlist")
    c.execute("ALTER TABLE reservation DROP COLUMN created_at")
    c.commit()
    c.close()
    rep = run(legacy_db, tmp_path / "app.db")
    assert rep["orgs"]["nareum"]["reservations"] == 2
    n = sqlite3.connect(tmp_path / "app.db")
    assert n.execute("SELECT use_waitlist FROM booths WHERE id=20").fetchone() == (0,)
