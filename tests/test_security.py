def test_temp_password_alphabet():
    from app.security import temp_password
    for _ in range(200):
        pw = temp_password()
        assert len(pw) == 10 and not set(pw) & set("0O1lI")


def test_username_rules():
    from app.security import normalize_username, valid_username
    assert normalize_username("  Hong.Gil ") == "hong.gil"
    assert valid_username("ab_1-2.c") and not valid_username("ab") and not valid_username("한글아이디")


def test_password_problem():
    from app.security import password_problem
    assert password_problem("short", "hong") is not None
    assert password_problem("Hong1234", "hong1234") is not None
    assert password_problem("goodpass1", "hong") is None


def test_link_token_length():
    from app.security import link_token
    assert len(link_token()) == 24


def test_guard_raises_after_limit(app):
    from app.ratelimit import guard
    from app.errors import ApiError
    import pytest
    with app.test_request_context():
        for _ in range(3):
            guard("k", 3, 60)
        with pytest.raises(ApiError) as e:
            guard("k", 3, 60)
        assert e.value.status == 429
