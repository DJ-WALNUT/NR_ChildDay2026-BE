PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def test_sniff():
    from app.assets import sniff
    assert (sniff(PNG), sniff(JPG), sniff(WEBP)) == ("png", "jpg", "webp")
    assert sniff(SVG) is None and sniff(b"GIF89a") is None and sniff(b"") is None


def test_store_is_content_addressed(app):
    from app.assets import store, path_for
    with app.app_context():
        a, b = store(PNG), store(PNG)
        assert a == b and a.endswith(".png") and len(a) == 68
        assert path_for(a).read_bytes() == PNG


def test_store_rejects(app):
    import pytest
    from app.assets import store
    from app.errors import ApiError, ValidationError
    with app.app_context():
        with pytest.raises(ValidationError):
            store(SVG)
        with pytest.raises(ApiError) as e:
            store(PNG + b"\x00" * 1_048_576)
        assert e.value.status == 413 and e.value.code == "file_too_large"


def test_path_for_rejects_bad_names(app):
    from app.assets import path_for
    with app.app_context():
        for bad in ("../app.db", "A" * 64 + ".png", "a" * 64 + ".svg", "a" * 63 + ".png"):
            assert path_for(bad) is None


def test_release_keeps_shared_file(app, db, make_org, make_event):
    from app.assets import store, release, path_for
    org = make_org()
    ev = make_event(org)
    with app.app_context():
        name = store(PNG)
    db.execute("UPDATE organizations SET logo=? WHERE id=?", (name, org))
    db.execute("UPDATE events SET logo=? WHERE id=?", (name, ev))
    db.execute("UPDATE organizations SET logo=NULL WHERE id=?", (org,))
    with app.app_context():
        release(name)
        assert path_for(name) is not None      # 행사가 아직 참조
    db.execute("UPDATE events SET logo=NULL WHERE id=?", (ev,))
    with app.app_context():
        release(name)
        assert path_for(name) is None
