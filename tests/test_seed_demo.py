import sqlite3

import pytest

from scripts.seed_demo import seed


def test_seed_creates_expected_counts(tmp_path):
    db = tmp_path / "data" / "app.db"
    out = seed(db)
    c = sqlite3.connect(db)
    q = lambda s: c.execute(s).fetchone()[0]
    assert q("SELECT COUNT(*) FROM organizations") == 2
    assert q("SELECT COUNT(*) FROM events") == 2
    assert q("SELECT COUNT(*) FROM booths") == 6
    assert q("SELECT COUNT(*) FROM partners") == 1
    assert q("SELECT COUNT(*) FROM booths WHERE partner_id IS NOT NULL") == 2
    assert q("SELECT COUNT(*) FROM reservations") == 30 == out["reservations"]
    assert q("SELECT COUNT(DISTINCT status) FROM reservations") == 4
    assert q("SELECT COUNT(*) FROM organizations WHERE logo IS NOT NULL") == 1
    assert q("SELECT MAX(LENGTH(name)) FROM events") > 40
    assert list((db.parent / "assets").glob("*.png"))


def test_seed_refuses_existing_path(tmp_path):
    db = tmp_path / "app.db"
    seed(db)
    with pytest.raises(SystemExit):
        seed(db)
