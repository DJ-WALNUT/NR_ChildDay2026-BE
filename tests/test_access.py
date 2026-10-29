import pytest

from app.errors import ApiError


def _partner(db, ev, linked=None, token="ptok", name="나름"):
    return db.execute(
        "INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
        (ev, name, linked, token)).lastrowid


def test_booth_access_matrix(app, make_org, make_event, make_booth, db):
    host, linked, other = make_org(), make_org(), make_org()
    ev = make_event(host)
    pid = _partner(db, ev, linked)
    b_assigned = make_booth(ev, partner_id=pid); b_free = make_booth(ev)
    from app.access import load_booth, booth_access
    from app.principal import Principal as P
    with app.test_request_context():
        A, F = load_booth(b_assigned), load_booth(b_free)
        u = lambda org: P(kind="user", label="u", user_id=1, org_id=org)
        assert booth_access(u(host), A) == "own"
        assert booth_access(u(linked), A) == "manage" and booth_access(u(linked), F) is None
        assert booth_access(u(other), A) is None
        assert booth_access(P(kind="partner_link", label="p", partner_id=pid), A) == "manage"
        assert booth_access(P(kind="partner_link", label="p", partner_id=pid), F) is None
        assert booth_access(P(kind="booth_link", label="b", booth_id=b_free), F) == "manage"
        assert booth_access(P(kind="booth_link", label="b", booth_id=b_free), A) is None
        root = P(kind="user", label="r", user_id=9, is_superadmin=True)
        assert booth_access(root, A) == "own"
        db.execute("UPDATE organizations SET is_active=0 WHERE id=?", (host,))
        assert booth_access(u(host), load_booth(b_assigned)) is None
        assert booth_access(u(linked), load_booth(b_assigned)) is None
        assert booth_access(root, load_booth(b_assigned)) == "own"


def test_require_booth_403_vs_404(app, make_org, make_event, make_booth, db):
    host, linked, other = make_org(), make_org(), make_org()
    ev = make_event(host)
    pid = _partner(db, ev, linked)
    b_assigned = make_booth(ev, partner_id=pid)
    from app.access import require_booth
    from app.principal import Principal as P
    with app.test_request_context():
        pl = P(kind="user", label="u", user_id=1, org_id=linked)
        po = P(kind="user", label="u", user_id=2, org_id=other)
        ph = P(kind="user", label="u", user_id=3, org_id=host)
        assert require_booth(b_assigned, need="manage", p=pl)["id"] == b_assigned
        assert require_booth(b_assigned, need="own", p=ph)["id"] == b_assigned
        with pytest.raises(ApiError) as e:
            require_booth(b_assigned, need="own", p=pl)
        assert e.value.status == 403
        with pytest.raises(ApiError) as e:
            require_booth(b_assigned, need="own", p=po)
        assert e.value.status == 404
        with pytest.raises(ApiError) as e:
            require_booth(99999, p=ph)
        assert e.value.status == 404


def test_bad_link_token_rate_limited(app):
    from app.access import resolve_booth_link
    with app.test_request_context(environ_base={"REMOTE_ADDR": "9.9.9.9"}):
        for _ in range(30):
            with pytest.raises(ApiError) as e:
                resolve_booth_link("nope")
            assert e.value.status == 404
        with pytest.raises(ApiError) as e:
            resolve_booth_link("nope")
        assert e.value.status == 429


def test_resolve_links_set_principal(app, make_org, make_event, make_booth, db):
    org = make_org()
    ev = make_event(org)
    pid = _partner(db, ev)
    b = make_booth(ev, name="체험", manage_token="btok")
    from app.access import resolve_booth_link, resolve_partner_link
    from app.principal import current_principal
    with app.test_request_context():
        p, booth = resolve_booth_link("btok")
        assert p.label == "부스 링크: 체험" and p.booth_id == b and p.org_id is None
        assert current_principal() == p and booth["org_name"]
        p, partner = resolve_partner_link("ptok")
        assert p.label == "실적 링크: 나름" and p.partner_id == pid
        assert current_principal() == p and partner["event_name"] == "행사"
        db.execute("UPDATE organizations SET is_active=0 WHERE id=?", (org,))
        with pytest.raises(ApiError) as e:
            resolve_partner_link("ptok")
        assert e.value.status == 404
        with pytest.raises(ApiError) as e:
            resolve_booth_link("btok")
        assert e.value.status == 404


def test_require_event(app, make_org, make_event, monkeypatch):
    import app.access as access
    from app.auth import User
    host, other = make_org(), make_org()
    ev = make_event(host)

    def run(user):
        monkeypatch.setattr(access, "current_user", lambda: user)
        with app.test_request_context():
            return access.require_event(ev)

    mk = lambda org, sa=False: User(id=1, username="x", display_name="x", org_id=org,
                                    is_superadmin=sa, must_change_password=False)
    assert run(mk(host))["org_name"]
    assert run(mk(None, True))["id"] == ev
    for bad in (mk(other), None):
        with pytest.raises(ApiError) as e:
            run(bad)
        assert e.value.status == 404
