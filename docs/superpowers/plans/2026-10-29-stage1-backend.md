# 1단계 백엔드 멀티테넌트 재구성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단일 파일 Flask 앱을 기관(테넌트)·계정·링크 권한을 갖춘 walnutphoto 구조의 백엔드로 재구성하고, 기존 DB를 새 스키마로 이관하는 스크립트를 만든다.

**Architecture:** Flask 앱 팩토리 + 블루프린트, ORM 없이 `sqlite3`(WAL, `BEGIN IMMEDIATE`). 요청마다 `Principal`(계정/부스 링크/실적 링크)을 만들고, 부스 작업은 `access.require_booth`로 권한을 확인한 뒤 `booth_ops`의 공통 함수로 처리한다. 정원 계산은 `capacity` 한 곳에서 한다.

**Tech Stack:** Python 3.11+, Flask 3.1, flask-cors, argon2-cffi, python-dotenv, gunicorn, pytest

**Spec:** `docs/superpowers/specs/2026-10-29-multitenant-design.md` (이하 "스펙"). 각 태스크는 스펙의 해당 절을 함께 읽는다.

**참고 구현:** `../../walnutphoto/server/app/` — `db.py`, `errors.py`, `security.py`, `ratelimit.py`, `auth.py`, `cli.py`, `__init__.py`, `housekeeping.py`, `tests/conftest.py`, `tests/test_isolation.py`. "옮긴다"라고 적힌 곳은 해당 파일을 복사한 뒤 적힌 차이만 반영한다.

## Global Constraints

- 작업 브랜치: `update`. 커밋 메시지 형식은 기존 저장소 관례 `[feat] …`, `[fix] …`, `[test] …`, `[docs] …`, `[chore] …`이며, 끝에 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 시간 값은 UNIX 초(REAL). "오늘"은 한국 시간(UTC+9) 기준 날짜.
- 오류 응답: `{"error": "<code>", "fields"?: {...}}`. 입력 검증 실패는 `422 invalid`.
- 모든 `/api/admin/` 변경 요청은 CSRF 헤더 `X-CSRF-Token` 필요(예외: `/api/admin/auth/login`).
- 다른 기관 자원 접근은 404, 범위 안이지만 소유 작업이 필요하면 403.
- 정원 점유 상태: `normal`, `completed`만. `noshow`, `waiting`은 점유하지 않는다.
- 슬롯 라벨: 타임 1개면 `'{h}시'`, 아니면 `'{h}시 {A..F}타임'`. 선착순은 `'선착순'`.
- 연령대: `0~8세`, `9~13세`, `14~16세`, `17~19세`, `20~24세`, `24세 이상`. 성별: `남`, `여`.
- 식별번호 `code`: 숫자만 남긴 뒤 정확히 4자리.
- 링크 토큰: `secrets.token_urlsafe(18)`, 원문 저장.
- 세션 쿠키 이름 `nr_session`, 유휴 12시간, 절대 7일.
- 로컬 Python이 3.14여도 Docker는 3.11이므로 3.12+ 전용 문법을 쓰지 않는다.
- 테스트 실행: 저장소 루트에서 `python -m pytest -q`.

## Review Focus

1. **신청이 있는 부스의 설정 변경**(타임 수 축소, 시간 범위 축소, 운영 방식 변경): 기존 신청은 지워지지 않고 명단·통계에 그대로 나오며, 남은 자리 계산은 현재 슬롯 목록만 보고, 사라진 슬롯으로의 새 신청은 422. → Task 6, Task 10에 테스트.
2. **이름·식별번호 입력 흔들림**(앞뒤 공백, `12-34`, `１２３４` 같은 비ASCII 숫자): 이름은 앞뒤 공백 제거, 식별번호는 ASCII 숫자만 남겨 4자리여야 하며, 신청과 조회가 같은 정규화를 쓴다. → Task 12에 테스트.
3. **아이디 대소문자·공백**: `" Hong "`으로 로그인해도 `hong` 계정으로 인식, 같은 아이디를 대소문자만 바꿔 발급하면 `username_taken`. → Task 3, Task 4에 테스트.
4. **운영 단체 삭제·연결 해제·기관 비활성화 직후**: 기존 실적 링크는 즉시 404, 배정됐던 부스는 미배정으로 남고, 연결 기관 계정은 그 부스에 404. → Task 8, Task 11에 테스트.
5. **한국 시간 자정 경계의 "오늘 신청"**: UTC 15:00(KST 다음날 00:00) 직후 신청은 다음날로 집계. → Task 13에 테스트.

---

## File Structure

```
NR_ChildDay2026-BE/
  app/
    __init__.py      create_app: 설정, migrate, ProxyFix, CORS, 블루프린트, 오류 처리, 보안 헤더, 정리 스레드
    config.py        Config (DATA_DIR, CORS_ORIGINS, SESSION_COOKIE_SECURE, TRUSTED_PROXY_HOPS, CLEANUP_INTERVAL)
    timeutil.py      now(), kst_date(ts)
    db.py            get_db, close_db, tx, migrate (walnutphoto에서 옮김)
    errors.py        ApiError, ValidationError(422 invalid), not_found, forbidden, conflict
    security.py      해시, 토큰, 임시 비밀번호, 아이디 규칙
    ratelimit.py     hit/exceeded/clear/clear_prefix/guard
    principal.py     Principal 데이터 클래스, current_principal()
    audit.py         audit(), list_audit()
    auth.py          세션, CSRF, 비밀번호 변경 의무 게이트, 데코레이터
    access.py        require_org/require_event/require_booth/booth_access, 링크 토큰 해석
    capacity.py      슬롯·점유·남은 자리·신청 상태 결정
    booth_ops.py     부스 관리 작업 공통 구현 + 통계·내보내기
    housekeeping.py  rate_events·세션 정리
    cli.py           flask create-superadmin
    routes/
      __init__.py    register_blueprints
      common.py      json_body(), parse helpers
      auth.py        /api/admin/auth/*
      admin_users.py /api/admin/orgs/<org>/users, /api/admin/users/<id>
      admin_events.py 행사·운영 단체·link-targets
      admin_booths.py 부스·명단·실적·overview·audit
      links.py       /api/link/*
      public.py      /api/public/*
      super.py       /api/admin/super/*
  migrations/001_init.sql
  scripts/migrate_legacy.py
  tests/ conftest.py + test_*.py (태스크별)
  legacy/app.py, legacy/fix_db.py   (Task 1에서 이동, Task 16에서 삭제)
  wsgi.py, gunicorn.conf.py, requirements.txt, pytest.ini, .env.example, Dockerfile, README.md
```

---

### Task 1: 프로젝트 골격, DB, 스키마

**Files:**
- Move: `app.py` → `legacy/app.py`, `fix_db.py` → `legacy/fix_db.py` (`git mv`; 이름 충돌 방지, 이관 참고용)
- Create: `requirements.txt`, `pytest.ini`, `.gitignore`(기존 + `data/`, `__pycache__/`, `.env`, `.pytest_cache/`), `app/__init__.py`, `app/config.py`, `app/timeutil.py`, `app/db.py`, `app/errors.py`, `app/routes/__init__.py`, `app/routes/common.py`, `migrations/001_init.sql`
- Test: `tests/conftest.py`, `tests/test_app.py`

**Interfaces:**
- Produces:
  - `create_app(overrides: dict | None = None) -> Flask` — `SECRET_KEY` 요구 없음. `DATA_DIR/app.db`에 migrate.
  - `timeutil.now() -> float`, `timeutil.kst_date(ts: float) -> datetime.date`
  - `db.get_db()`, `db.tx()`(컨텍스트 매니저), `db.migrate(path) -> int` — walnutphoto와 동일.
  - `errors.ApiError(status, code, extra=None)`, `errors.ValidationError(fields: dict[str,str])` → `422 {"error":"invalid","fields":...}`, `not_found()`, `forbidden()`, `conflict(code: str)` → 409.
  - `routes.common.json_body() -> dict` (dict가 아니면 `{}`)
  - conftest fixtures: `app`, `client`, `db`(앱 컨텍스트 안의 연결)
  - `GET /api/health` → `{"ok": true}`

- [ ] **Step 1: `requirements.txt`, `pytest.ini` 작성**

  `requirements.txt`(런타임): `Flask>=3.1,<4`, `flask-cors>=5`, `argon2-cffi>=23.1`, `python-dotenv>=1.0`, `gunicorn>=23`. `requirements-dev.txt`: `-r requirements.txt` + `pytest>=8`. `pytest.ini`는 walnutphoto와 동일(`pythonpath = .`, `testpaths = tests`). `pip install -r requirements-dev.txt`.

- [ ] **Step 2: 실패하는 테스트 작성** (`tests/test_app.py`)

```python
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json == {"ok": True}

def test_schema_tables(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"organizations", "users", "sessions", "events", "partners", "booths",
            "reservations", "audit_log", "rate_events", "schema_version"} <= names

def test_unknown_api_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json == {"error": "not_found"}

def test_validation_error_shape(app):
    from app.errors import ValidationError
    e = ValidationError({"name": "필수"})
    assert e.status == 422 and e.body() == {"error": "invalid", "fields": {"name": "필수"}}

def test_kst_date_boundary():
    from app.timeutil import kst_date
    import datetime as dt
    ts = dt.datetime(2026, 5, 4, 15, 0, 1, tzinfo=dt.timezone.utc).timestamp()
    assert kst_date(ts) == dt.date(2026, 5, 5)
```

- [ ] **Step 3: 실패 확인** — `python -m pytest tests/test_app.py -q` → `ModuleNotFoundError: app` 또는 import 실패.

- [ ] **Step 4: 구현**
  - `migrations/001_init.sql`: 스펙 4절의 SQL 전체(감사 로그 확장 컬럼 포함)를 그대로 옮긴다.
  - `db.py`, `errors.py`: walnutphoto에서 옮기고, `ValidationError`는 `422 "invalid"`로, `conflict()`를 추가한다.
  - `config.py`: `DATA_DIR`(기본 `<repo>/data`), `CORS_ORIGINS`(쉼표 구분, 기본 `http://localhost:5173,https://nrbooth.team-cluster.kr,https://2026child.team-cluster.kr`), `SESSION_COOKIE_SECURE`(기본 1), `TRUSTED_PROXY_HOPS`(기본 2), `CLEANUP_INTERVAL`(기본 600).
  - `__init__.py`: walnutphoto의 팩토리에서 SECRET_KEY 검사·ProxyClientIp·업로드 크기·ADMIN_DIST·사진 정리를 뺀 형태. `/api/health`는 팩토리 안에 직접 정의. CORS·보안 헤더·정리 스레드는 Task 14에서 추가.
  - `conftest.py`: `create_app({"TESTING": True, "DATA_DIR": str(tmp_path/"data"), "PREFERRED_URL_SCHEME": "https", "CLEANUP_INTERVAL": 0})`.

- [ ] **Step 5: 통과 확인** — `python -m pytest -q` → 5 passed.

- [ ] **Step 6: 커밋** — `[chore] 백엔드 패키지 골격과 스키마 추가`

---

### Task 2: 보안 도구, 속도 제한, Principal, 감사 로그

**Files:**
- Create: `app/security.py`, `app/ratelimit.py`, `app/principal.py`, `app/audit.py`
- Test: `tests/test_security.py`, `tests/test_audit.py`

**Interfaces:**
- Produces:
  - `security.hash_password(pw) -> str`, `verify_password(stored, pw) -> bool`, `new_token(nbytes=32) -> str`, `sha256_hex(s) -> str`, `consteq(a, b) -> bool` (walnutphoto와 동일)
  - `security.link_token() -> str` = `secrets.token_urlsafe(18)`
  - `security.temp_password() -> str` — 알파벳 `"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"`에서 `secrets.choice` 10자.
  - `security.normalize_username(s: str) -> str` = `s.strip().lower()`; `valid_username(s) -> bool` = `^[a-z0-9._-]{3,32}$`
  - `security.MIN_PASSWORD = 8`; `password_problem(pw: str, username: str) -> str | None` — 8자 미만이면 `"8자 이상이어야 합니다."`, 아이디와 같으면(대소문자 무시) `"아이디와 같을 수 없습니다."`.
  - `ratelimit.hit/exceeded/clear/clear_prefix` (walnutphoto와 동일) + `guard(key, limit, window_sec) -> None` — 초과 시 `ApiError(429, "rate_limited")`, 아니면 `hit(key)`.
  - `principal.Principal` (frozen dataclass): `kind: Literal["user","booth_link","partner_link"]`, `label: str`, `user_id: int | None = None`, `org_id: int | None = None`(계정의 기관), `is_superadmin: bool = False`, `booth_id: int | None = None`, `partner_id: int | None = None`.
  - `principal.set_principal(p)`, `principal.current_principal() -> Principal | None` (flask `g`에 저장)
  - `audit.audit(p: Principal | None, action: str, target: str | None, summary: str, *, org_id: int | None, detail: dict | None = None) -> None` — `p is None`이면 actor `'system'`, label `'시스템'`. `actor_org_id = p.org_id`(링크·슈퍼관리자는 None), `user_id = p.user_id`. detail은 `json.dumps(ensure_ascii=False)`.
  - `audit.list_audit(*, org_id: int | None, before: int | None, limit: int, action: str | None) -> dict` — `org_id`가 주어지면 `org_id = ? OR actor_org_id = ?`, None이면 전체. `action`은 접두사 일치(`substr`). `id < before`, `ORDER BY id DESC`, `limit`은 1~200으로 자른다. 반환 `{"items": [...], "next_before": 마지막 id 또는 None(더 없을 때)}`. `detail`은 dict로 파싱해서 돌려준다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_security.py
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
    with app.test_request_context():
        for _ in range(3):
            guard("k", 3, 60)
        import pytest
        with pytest.raises(ApiError) as e:
            guard("k", 3, 60)
        assert e.value.status == 429

# tests/test_audit.py
def test_audit_scope_and_paging(app, db):
    from app.audit import audit, list_audit
    from app.principal import Principal
    with app.test_request_context():
        p = Principal(kind="user", label="홍(hong)", user_id=1, org_id=2)
        for i in range(3):
            audit(p, "booth.update", f"booth:{i}", f"변경 {i}", org_id=1)   # 행위자 기관 2가 기관 1 부스 수정
        audit(None, "org.create", "org:9", "기관 생성", org_id=9)
        a = list_audit(org_id=2, before=None, limit=2, action=None)
        assert [i["summary"] for i in a["items"]] == ["변경 2", "변경 1"] and a["next_before"] is not None
        b = list_audit(org_id=2, before=a["next_before"], limit=2, action=None)
        assert [i["summary"] for i in b["items"]] == ["변경 0"] and b["next_before"] is None
        assert len(list_audit(org_id=1, before=None, limit=50, action="booth.")["items"]) == 3
        assert list_audit(org_id=1, before=None, limit=50, action="org.")["items"] == []
        sys = list_audit(org_id=None, before=None, limit=50, action="org.")["items"][0]
        assert sys["actor"] == "system" and sys["actor_label"] == "시스템"
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_security.py tests/test_audit.py -q` → import 오류.
- [ ] **Step 3: 구현** — 위 시그니처대로. `ratelimit`, `security`는 walnutphoto에서 옮긴 뒤 추가 함수만 더한다.
- [ ] **Step 4: 통과 확인** — 같은 명령 → 전부 PASS.
- [ ] **Step 5: 커밋** — `[feat] 보안 도구, 속도 제한, 감사 로그 모듈 추가`

---

### Task 3: 인증 (로그인, 세션, CSRF, 비밀번호 변경 의무, 슈퍼관리자 CLI)

**Files:**
- Create: `app/auth.py`, `app/routes/auth.py`, `app/cli.py`
- Modify: `app/__init__.py`(init_auth, init_cli, 블루프린트 등록), `app/routes/__init__.py`
- Test: `tests/conftest.py`(팩토리 추가), `tests/test_auth.py`

**Interfaces:**
- Consumes: Task 2 전부.
- Produces:
  - `auth.User` dataclass: `id, username, display_name, org_id: int | None, is_superadmin: bool, must_change_password: bool`
  - `auth.current_user() -> User | None`, `auth.login_required`, `auth.superadmin_required`
  - `auth.user_principal(u: User) -> Principal` — label `f"{display_name}({username})"`, `org_id=u.org_id`(슈퍼관리자는 None).
  - `auth.revoke_sessions(user_id: int, keep_id_hash: str | None = None) -> None`
  - `auth.init_auth(app)` — `before_request`에 ① CSRF 검사(walnutphoto 방식, 예외 정규식은 `/api/admin/auth/login`만) ② 로그인된 요청이 `must_change_password`이고 경로가 `/api/admin/` 아래이면서 `/api/admin/auth/(me|password|logout)`이 아니면 `403 password_change_required` ③ 로그인되어 있으면 `set_principal(user_principal(u))`.
  - 세션 로드 시 `users.is_active = 0` 또는 소속 기관 `is_active = 0`이면 세션을 지우고 비로그인으로 취급(슈퍼관리자는 기관 검사 제외).
  - 라우트(`/api/admin/auth`):
    - `POST /login {username, password}` → `200 {user, csrf}` + 쿠키. 아이디 정규화, 제한 키 `login:{username}:{ip}` 15분 10회, 실패·비활성 계정·비활성 기관 모두 `401 invalid_credentials`. 성공 시 감사 `auth.login`(org_id=계정 기관).
    - `POST /logout` → 204
    - `GET /me` → `{user, csrf, org: {id,name,slug} | null}`
    - `POST /password {current, new}` → 204. 현재 비밀번호 불일치 `401 invalid_credentials`(로그인과 같은 제한 키 사용), `password_problem`이면 `422 invalid {fields:{new}}`. 성공 시 해시 갱신, `must_change_password=0`, `password_changed_at`, 현재 세션 외 폐기, 감사 `auth.password_change`.
  - `user` JSON: `{id, username, display_name, org_id, is_superadmin, must_change_password}`
  - CLI `flask create-superadmin --username --password` (walnutphoto `cli.py`를 아이디 기준으로; 슈퍼관리자는 `must_change_password=0`, `display_name`은 아이디).
  - conftest 팩토리: `make_org(name="기관", slug=None, active=True) -> int`, `make_user(username="hong", password="password1", org_id=None, superadmin=False, must_change=False, active=True) -> int`(display_name은 아이디), `login_as(client, username, password="password1") -> csrf`(client.csrf 설정), `api(client, method, url, json=None)`(CSRF 헤더 자동) — walnutphoto conftest 방식.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_auth.py`)

```python
def test_login_me_logout(client, make_org, make_user, login_as, api):
    org = make_org(slug="nareum"); make_user("hong", org_id=org)
    login_as(client, " HONG ")
    me = client.get("/api/admin/auth/me").json
    assert me["user"]["username"] == "hong" and me["org"]["slug"] == "nareum"
    assert api(client, "POST", "/api/admin/auth/logout").status_code == 204
    assert client.get("/api/admin/auth/me").status_code == 401

def test_wrong_password_and_rate_limit(client, make_org, make_user):
    make_user("hong", org_id=make_org())
    for _ in range(10):
        assert client.post("/api/admin/auth/login", json={"username": "hong", "password": "x"}).status_code == 401
    r = client.post("/api/admin/auth/login", json={"username": "hong", "password": "password1"})
    assert r.status_code == 429

def test_inactive_user_or_org_cannot_login(client, make_org, make_user):
    make_user("a1", org_id=make_org(), active=False)
    make_user("b1", org_id=make_org(active=False))
    for u in ("a1", "b1"):
        r = client.post("/api/admin/auth/login", json={"username": u, "password": "password1"})
        assert r.status_code == 401 and r.json["error"] == "invalid_credentials"

def test_csrf_required(client, make_org, make_user, login_as):
    make_user("hong", org_id=make_org()); login_as(client, "hong")
    assert client.post("/api/admin/auth/logout").json["error"] == "csrf"

def test_must_change_password_gate(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org, must_change=True); login_as(client, "hong")
    r = api(client, "GET", f"/api/admin/orgs/{org}/users")
    assert r.status_code == 403 and r.json["error"] == "password_change_required"
    assert client.get("/api/admin/auth/me").json["user"]["must_change_password"] is True
    r = api(client, "POST", "/api/admin/auth/password", json={"current": "password1", "new": "hong"})
    assert r.status_code == 422
    assert api(client, "POST", "/api/admin/auth/password",
               json={"current": "password1", "new": "newpass99"}).status_code == 204
    assert client.get("/api/admin/auth/me").json["user"]["must_change_password"] is False

def test_password_change_revokes_other_sessions(app, make_org, make_user, login_as, api):
    make_user("hong", org_id=make_org())
    c1, c2 = app.test_client(), app.test_client()
    login_as(c1, "hong"); login_as(c2, "hong")
    api(c1, "POST", "/api/admin/auth/password", json={"current": "password1", "new": "newpass99"})
    assert c1.get("/api/admin/auth/me").status_code == 200
    assert c2.get("/api/admin/auth/me").status_code == 401

def test_deactivated_mid_session(client, db, make_org, make_user, login_as):
    uid = make_user("hong", org_id=make_org()); login_as(client, "hong")
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
    assert client.get("/api/admin/auth/me").status_code == 401

def test_cli_create_superadmin(app):
    r = app.test_cli_runner().invoke(args=["create-superadmin", "--username", "Root", "--password", "rootpass1"])
    assert r.exit_code == 0
    c = app.test_client()
    me = c.post("/api/admin/auth/login", json={"username": "root", "password": "rootpass1"}).json
    assert me["user"]["is_superadmin"] is True and me["user"]["must_change_password"] is False
```

  (`/api/admin/orgs/<org>/users`는 Task 4에서 만들지만, 게이트는 라우트 존재와 무관하게 `before_request`에서 걸리므로 이 테스트는 지금 통과해야 한다.)

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_auth.py -q` → FAIL.
- [ ] **Step 3: 구현** — walnutphoto `auth.py`를 옮기고 이메일→아이디, `User` 필드, 활성 검사, 변경 의무 게이트, `/password` 라우트(walnutphoto `routes/passwords.py`의 `change_password` 참고)를 반영한다. 메일·초대·재설정 관련 코드는 옮기지 않는다.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q` → 전부 PASS.
- [ ] **Step 5: 커밋** — `[feat] 아이디 기반 로그인, 세션, CSRF, 비밀번호 변경 의무 추가`

---

### Task 4: 기관 계정 관리

**Files:**
- Create: `app/access.py`(이 태스크에서는 `require_org`만), `app/routes/admin_users.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Consumes: `auth.current_user`, `auth.revoke_sessions`, `principal.current_principal`, `audit.audit`, `security.*`
- Produces:
  - `access.require_org(org_id: int) -> sqlite3.Row` — 현재 사용자 기준. 기관이 없거나 `user.org_id != org_id`(슈퍼관리자 제외)면 404.
  - `access.require_user_target(user_id: int) -> sqlite3.Row` — 대상 계정이 없거나, 슈퍼관리자 계정이거나(요청자가 슈퍼관리자가 아닐 때), 다른 기관이면 404.
  - 라우트:
    - `GET /api/admin/orgs/<org>/users` → `{"users": [{id, username, display_name, is_active, must_change_password, last_login_at, created_at}]}` 아이디순
    - `POST /api/admin/orgs/<org>/users {username, display_name}` → `201 {"user": {...}, "temp_password": "..."}`. 아이디 규칙 위반 `422 {fields:{username}}`, display_name 1~40자, 중복 `409 username_taken`. `must_change_password=1`, `created_by`. 감사 `user.create`.
    - `PATCH /api/admin/users/<id> {display_name?, is_active?}` → `{"user": {...}}`. 자기 자신 비활성화 `409 cannot_deactivate_self`, 그 기관의 마지막 활성 계정 비활성화 `409 last_active_user`. 비활성화 시 `revoke_sessions`. 감사 `user.update` / `user.deactivate` / `user.activate`.
    - `POST /api/admin/users/<id>/reset-password` → `{"temp_password": "..."}`. 해시 교체, `must_change_password=1`, 대상 세션 전부 폐기. 자기 자신 대상은 `409 use_password_change`. 감사 `user.reset_password`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_users.py`)

```python
def test_issue_account_and_first_login(client, app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "Kim", "display_name": "김담당"})
    assert r.status_code == 201 and r.json["user"]["username"] == "kim"
    c2 = app.test_client()
    me = c2.post("/api/admin/auth/login", json={"username": "kim", "password": r.json["temp_password"]}).json
    assert me["user"]["must_change_password"] is True

def test_duplicate_username_case_insensitive(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "HONG", "display_name": "x"})
    assert r.status_code == 409 and r.json["error"] == "username_taken"

def test_reset_password_revokes_and_forces_change(app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); kim = make_user("kim", org_id=org)
    ck, ch = app.test_client(), app.test_client()
    login_as(ck, "kim"); login_as(ch, "hong")
    temp = api(ch, "POST", f"/api/admin/users/{kim}/reset-password").json["temp_password"]
    assert ck.get("/api/admin/auth/me").status_code == 401
    me = app.test_client().post("/api/admin/auth/login", json={"username": "kim", "password": temp}).json
    assert me["user"]["must_change_password"] is True

def test_last_active_and_self_guard(client, make_org, make_user, login_as, api):
    org = make_org(); me = make_user("hong", org_id=org); other = make_user("kim", org_id=org)
    login_as(client, "hong")
    assert api(client, "PATCH", f"/api/admin/users/{me}", json={"is_active": False}).json["error"] == "cannot_deactivate_self"
    assert api(client, "PATCH", f"/api/admin/users/{other}", json={"is_active": False}).status_code == 200
    # 이제 hong이 유일한 활성 계정: 슈퍼관리자가 비활성화하려 해도 막힌다
    make_user("root", superadmin=True); login_as(client, "root")
    assert api(client, "PATCH", f"/api/admin/users/{me}", json={"is_active": False}).json["error"] == "last_active_user"

def test_cross_org_and_superadmin_targets_hidden(client, make_org, make_user, login_as, api):
    a, b = make_org(), make_org()
    make_user("hong", org_id=a); other = make_user("kim", org_id=b); root = make_user("root", superadmin=True)
    login_as(client, "hong")
    assert api(client, "GET", f"/api/admin/orgs/{b}/users").status_code == 404
    assert api(client, "POST", f"/api/admin/users/{other}/reset-password").status_code == 404
    assert api(client, "POST", f"/api/admin/users/{root}/reset-password").status_code == 404

def test_superadmin_manages_any_org(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "POST", f"/api/admin/orgs/{org}/users", json={"username": "first", "display_name": "첫 담당"})
    assert r.status_code == 201
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_users.py -q`
- [ ] **Step 3: 구현** — 마지막 활성 계정 검사와 갱신은 하나의 `tx()` 안에서 한다.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 기관 계정 발급·초기화·비활성화 API 추가`

---

### Task 5: 슈퍼관리자 기관 관리, 백업

**Files:**
- Create: `app/routes/super.py`
- Test: `tests/test_super.py`

**Interfaces:**
- Produces (모두 `@superadmin_required`, prefix `/api/admin/super`):
  - `GET /orgs` → `{"orgs": [{id, name, slug, is_active, created_at, user_count, event_count}]}`
  - `POST /orgs {name, slug}` → `201 {"org": {...}}`. slug 규칙 `^[a-z0-9][a-z0-9-]{1,31}$`, 예약어 `manage, api, b, p, reserve, check, assets, login` 금지(`422 {fields:{slug}}`), 중복 `409 slug_taken`. 감사 `org.create`(org_id=새 기관).
  - `PATCH /orgs/<id> {name?, slug?, is_active?}` → `{"org": {...}}`. 감사 `org.update`(detail에 변경 전후).
  - `GET /backup` → `application/octet-stream`, 파일명 `nrbooth-backup-YYYYMMDD-HHMM.db`. `sqlite3.Connection.backup()`으로 임시 파일에 일관된 사본을 만든 뒤 전송. 감사 `backup.download`.
  - `GET /audit` — Task 13에서 추가.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_super.py`)

```python
def test_owner_gets_403_on_super(client, make_org, make_user, login_as, api):
    make_user("hong", org_id=make_org()); login_as(client, "hong")
    assert api(client, "GET", "/api/admin/super/orgs").status_code == 403

def test_create_and_rename_org(client, make_user, login_as, api):
    make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "POST", "/api/admin/super/orgs", json={"name": "나름", "slug": "nareum"})
    assert r.status_code == 201
    assert api(client, "POST", "/api/admin/super/orgs", json={"name": "x", "slug": "nareum"}).json["error"] == "slug_taken"
    assert api(client, "POST", "/api/admin/super/orgs", json={"name": "x", "slug": "manage"}).status_code == 422
    oid = r.json["org"]["id"]
    assert api(client, "PATCH", f"/api/admin/super/orgs/{oid}", json={"name": "나름센터"}).json["org"]["name"] == "나름센터"

def test_deactivate_org_logs_out_members(app, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); make_user("root", superadmin=True)
    ch, cr = app.test_client(), app.test_client()
    login_as(ch, "hong"); login_as(cr, "root")
    api(cr, "PATCH", f"/api/admin/super/orgs/{org}", json={"is_active": False})
    assert ch.get("/api/admin/auth/me").status_code == 401

def test_backup_is_sqlite(client, make_user, login_as, api):
    make_user("root", superadmin=True); login_as(client, "root")
    r = api(client, "GET", "/api/admin/super/backup")
    assert r.status_code == 200 and r.data[:16] == b"SQLite format 3\x00"
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_super.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 슈퍼관리자 기관 관리와 백업 API 추가`

---

### Task 6: 정원 계산 (`capacity`)

**Files:**
- Create: `app/capacity.py`
- Test: `tests/test_capacity.py`

**Interfaces:**
- Produces:
  - `OCCUPYING = ("normal", "completed")`, `FCFS_SLOT = "선착순"`
  - `slot_labels(start_hour: int, end_hour: int, slots_per_hour: int) -> list[str]` — 양 끝 포함, 타임 1개면 `"11시"`, 아니면 `"11시 A타임"`.
  - `occupancy(db, booth_id: int) -> dict[str, int]` — 점유 상태만, 슬롯별 개수.
  - `availability(booth: Mapping, occ: dict[str,int]) -> dict` — `booth`는 부스 행(키: `mode, use_waitlist, total_limit, start_hour, end_hour, slots_per_hour, limit_per_slot, is_active`).
    - time: `{"slots": [{"label", "remaining": int | None, "full": bool}], "is_closed": bool}` — `limit_per_slot == 0`이면 `remaining None, full False`. 현재 `slot_labels`에 없는 슬롯의 점유는 무시.
    - fcfs: `{"remaining": int | None, "full": bool, "is_closed": bool}`
    - `is_closed = not is_active or (not use_waitlist and 모든 슬롯 full)` (fcfs는 `full`). 슬롯이 0개면 닫힘.
  - `decide_status(booth: Mapping, occ: dict[str,int], slot: str | None) -> tuple[str, str]` → `(slot, status)`. 비활성 → `ApiError(409,"closed")`. time인데 slot이 목록에 없으면 `ValidationError({"slot": "선택할 수 없는 시간입니다."})`. fcfs는 slot 인자를 무시하고 `FCFS_SLOT`. 꽉 찼으면 `use_waitlist`이면 `"waiting"`, 아니면 `ApiError(409,"full")`. 아니면 `"normal"`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from app.capacity import slot_labels, availability, decide_status
import pytest
from app.errors import ApiError, ValidationError

def B(**kw):
    base = dict(mode="time", use_waitlist=0, total_limit=0, start_hour=11, end_hour=12,
                slots_per_hour=2, limit_per_slot=2, is_active=1)
    return {**base, **kw}

def test_labels():
    assert slot_labels(11, 12, 2) == ["11시 A타임", "11시 B타임", "12시 A타임", "12시 B타임"]
    assert slot_labels(11, 11, 1) == ["11시"]

def test_time_availability_and_orphan_slot_ignored():
    a = availability(B(), {"11시 A타임": 2, "15시 C타임": 9})
    assert a["slots"][0] == {"label": "11시 A타임", "remaining": 0, "full": True}
    assert a["slots"][1]["remaining"] == 2 and a["is_closed"] is False

def test_closed_when_all_full_without_waitlist():
    occ = {l: 2 for l in slot_labels(11, 12, 2)}
    assert availability(B(), occ)["is_closed"] is True
    assert availability(B(use_waitlist=1), occ)["is_closed"] is False

def test_decide():
    occ = {"11시 A타임": 2}
    with pytest.raises(ApiError) as e: decide_status(B(), occ, "11시 A타임")
    assert e.value.code == "full"
    assert decide_status(B(use_waitlist=1), occ, "11시 A타임") == ("11시 A타임", "waiting")
    assert decide_status(B(), occ, "11시 B타임") == ("11시 B타임", "normal")
    with pytest.raises(ValidationError): decide_status(B(), occ, "15시 C타임")
    with pytest.raises(ApiError) as e: decide_status(B(is_active=0), {}, "11시 B타임")
    assert e.value.code == "closed"

def test_fcfs():
    f = B(mode="fcfs", total_limit=3)
    assert availability(f, {"선착순": 3}) == {"remaining": 0, "full": True, "is_closed": True}
    assert decide_status(f, {"선착순": 2}, "아무거나") == ("선착순", "normal")
    assert availability(B(mode="fcfs", total_limit=0), {"선착순": 99})["full"] is False

def test_occupancy_excludes_noshow_waiting(app, db):
    from app.capacity import occupancy
    # 기관·행사·부스 1개와 신청 4건(normal, completed, noshow, waiting)을 같은 슬롯에 삽입한 뒤
    # occupancy(db, booth_id) == {"11시 A타임": 2}
```

  (`test_occupancy_excludes_noshow_waiting`은 conftest에 `make_event(org_id, name="행사") -> int`, `make_booth(event_id, **fields) -> int`(manage_token 자동, 기본은 스키마 기본값), `make_reservation(booth_id, name, code="1234", slot="11시 A타임", status="normal", gender="남", age_group="9~13세", created_at=None) -> int` 팩토리를 추가해서 작성한다. 이후 태스크는 이 팩토리를 쓴다.)

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_capacity.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 정원 계산 모듈 추가`

---

### Task 7: 접근 판단 (`access`)

**Files:**
- Modify: `app/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `principal.Principal`, `current_principal`, `ratelimit.guard`
- Produces:
  - `BOOTH_SQL` 결과 행 키: 부스 전 컬럼 + `event_name, org_id, org_name, org_slug, org_active, partner_name, partner_linked_org_id`
  - `load_booth(booth_id: int) -> sqlite3.Row | None`
  - `booth_access(p: Principal, booth: Row) -> Literal["own","manage"] | None`
    - 기관 비활성(`org_active=0`)이면: 슈퍼관리자만 `"own"`, 나머지 None.
    - user: 슈퍼관리자 또는 `p.org_id == booth.org_id` → `"own"`; `partner_linked_org_id == p.org_id` → `"manage"`.
    - booth_link: `p.booth_id == booth.id` → `"manage"`.
    - partner_link: `p.partner_id == booth.partner_id` → `"manage"`.
  - `require_booth(booth_id: int, need: Literal["manage","own"] = "manage", p: Principal | None = None) -> Row` — p 기본은 `current_principal()`. 없음/None 접근 → 404, `need="own"`인데 `"manage"` → 403.
  - `require_event(event_id: int) -> Row` — 현재 사용자가 행사 소유 기관이거나 슈퍼관리자. 행 키: 행사 컬럼 + `org_name`.
  - `resolve_booth_link(token: str) -> tuple[Principal, Row]` — 토큰 불일치·기관 비활성 → `guard(f"link-miss:{ip}", 30, 900)` 후 404. Principal label `f"부스 링크: {booth.name}"`, `org_id=None`. `set_principal` 호출.
  - `resolve_partner_link(token: str) -> tuple[Principal, Row]` — partners 행 + `event_name, org_id, org_name, org_active`. label `f"실적 링크: {partner.name}"`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_access.py`) — 함수 단위 테스트, `app.test_request_context()` 안에서 실행.

```python
def test_booth_access_matrix(app, make_org, make_event, make_booth, db):
    host, linked, other = make_org(), make_org(), make_org()
    ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at)"
                     " VALUES (?,?,?,?,0)", (ev, "나름", linked, "ptok")).lastrowid
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
        assert booth_access(P(kind="user", label="r", user_id=9, is_superadmin=True), A) == "own"
        db.execute("UPDATE organizations SET is_active=0 WHERE id=?", (host,))
        assert booth_access(u(host), load_booth(b_assigned)) is None

def test_require_booth_403_vs_404(app, make_org, make_event, make_booth, db):
    # 연결 기관 사용자로 require_booth(need="own") → 403, 무관 기관 → 404
    ...

def test_bad_link_token_rate_limited(app):
    # resolve_booth_link("nope") 31번째 호출은 429, 그 전은 404
    ...
```

  (`...`로 표시한 두 테스트는 위 패턴 그대로: 첫 번째는 `ApiError.status`를 403/404로, 두 번째는 반복 호출로 404 30회 후 429를 단언한다.)

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_access.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 계정·링크 공통 접근 판단 추가`

---

### Task 8: 행사·운영 단체 API

**Files:**
- Create: `app/routes/admin_events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `require_org`, `require_event`, `audit`, `security.link_token`
- Produces:
  - `GET /api/admin/orgs/<org>/events` → `{"events": [{id, name, created_at, booth_count, partner_count}]}` 최신순
  - `POST /api/admin/orgs/<org>/events {name}` → 201 `{"event": {...}}` (이름 1~100자). 감사 `event.create`.
  - `PATCH /api/admin/events/<id> {name}` → `{"event"}`. 감사 `event.update`.
  - `DELETE /api/admin/events/<id>` → 204. 감사 `event.delete`(detail에 부스 수·신청 수).
  - `GET /api/admin/events/<id>/partners` → `{"partners": [{id, name, linked_org: {id,name} | null, access_token, booth_count}]}`
  - `POST /api/admin/events/<id>/partners {name, linked_org_id?}` → 201. `linked_org_id`는 존재하는 활성 기관이어야 함(아니면 422). 감사 `partner.create`.
  - `PATCH /api/admin/partners/<id> {name?, linked_org_id? (null 허용)}` · `DELETE /api/admin/partners/<id>`(배정 부스는 `ON DELETE SET NULL`로 미배정) · `POST /api/admin/partners/<id>/rotate-token` → `{"access_token"}`. 모두 행사 소유자. 감사 `partner.update/delete/rotate_token`.
  - `GET /api/admin/orgs/<org>/link-targets` → `{"orgs": [{id, name}]}` 활성 기관 전체(자기 기관 제외).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_events.py`)

```python
def test_event_crud_and_cascade(client, db, make_org, make_user, login_as, api, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{org}/events", json={"name": "어울마당"}).json["event"]["id"]
    b = make_booth(ev); make_reservation(b, "가")
    assert api(client, "DELETE", f"/api/admin/events/{ev}").status_code == 204
    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0

def test_partner_link_lifecycle(client, db, make_org, make_user, login_as, api, make_booth):
    host, linked = make_org(), make_org(); make_user("hong", org_id=host); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{host}/events", json={"name": "난장"}).json["event"]["id"]
    p = api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "나름", "linked_org_id": linked}).json["partner"]
    b = make_booth(ev, partner_id=p["id"])
    old = p["access_token"]
    new = api(client, "POST", f"/api/admin/partners/{p['id']}/rotate-token").json["access_token"]
    assert new != old
    assert api(client, "DELETE", f"/api/admin/partners/{p['id']}").status_code == 204
    assert db.execute("SELECT partner_id FROM booths WHERE id=?", (b,)).fetchone()[0] is None

def test_link_inactive_org_rejected(client, make_org, make_user, login_as, api):
    host, dead = make_org(), make_org(active=False); make_user("hong", org_id=host); login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{host}/events", json={"name": "난장"}).json["event"]["id"]
    assert api(client, "POST", f"/api/admin/events/{ev}/partners", json={"name": "x", "linked_org_id": dead}).status_code == 422
    ids = [o["id"] for o in api(client, "GET", f"/api/admin/orgs/{host}/link-targets").json["orgs"]]
    assert dead not in ids and host not in ids
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_events.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 행사·운영 단체 API 추가`

---

### Task 9: 부스 API (생성, 일괄 등록, 수정, 삭제, 토글, 일괄 상태, 재발급)

**Files:**
- Create: `app/booth_ops.py`(이 태스크에서는 `validate_settings`, `booth_json`, `update_settings`, `toggle`), `app/routes/admin_booths.py`
- Test: `tests/test_booths.py`

**Interfaces:**
- Consumes: `require_event`, `require_booth`, `capacity.occupancy/availability`, `audit`
- Produces (`booth_ops`):
  - `SETTING_FIELDS = ("name","mode","use_waitlist","total_limit","start_hour","end_hour","slots_per_hour","limit_per_slot")`
  - `validate_settings(body: dict, base: Mapping | None = None) -> dict` — 주어진 키만 검증해 정규화한 dict 반환(`base`와 합친 결과로 시간 범위를 검사). 규칙: name 1~100자(앞뒤 공백 제거), mode ∈ time/fcfs, bool은 0/1, 정수 0 이상, `0 <= start_hour <= end_hour <= 23`, `1 <= slots_per_hour <= 6`. 위반 시 `ValidationError`(필드별 메시지).
  - `booth_json(booth: Row, occ: dict | None = None) -> dict` — `{id, event_id, event_name, org_id, partner_id, partner_name, name, mode, use_waitlist(bool), total_limit, start_hour, end_hour, slots_per_hour, limit_per_slot, is_active(bool), manage_token, count, availability}`. `count`는 `noshow` 제외 신청 수.
  - `update_settings(p, booth: Row, body: dict) -> Row` — 감사 `booth.update`(summary 예 `"페이스페인팅 설정 변경: 정원 100 → 120"`, detail에 변경 전후).
  - `toggle(p, booth: Row) -> bool` — 새 `is_active`. 감사 `booth.toggle`.
- Produces (라우트, `/api/admin`):
  - `GET /events/<id>/booths` → `{"booths": [booth_json...]}` 최신순 (require_event)
  - `POST /events/<id>/booths {설정 필드, partner_id?}` → 201 `{"booth"}`. partner_id는 같은 행사 소속이어야 함(아니면 422). 감사 `booth.create`.
  - `POST /events/<id>/booths/bulk {rows: [...]}` — 각 행은 단일 생성과 같은 필드. 전부 `validate_settings`로 검증, 실패 행이 하나라도 있으면 `422 {"error":"invalid","fields":{"rows":{"<index>":"<첫 번째 메시지>"}}}`이고 아무것도 저장하지 않음. 성공 시 201 `{"created": n}`. 감사 `booth.bulk_create` 1건.
  - `GET /booths/<id>` (manage) → `{"booth"}`
  - `PATCH /booths/<id>` — `SETTING_FIELDS`는 manage, `event_id`·`partner_id`는 own(같은 기관의 행사·해당 행사의 운영 단체만 허용, 행사 이동 시 partner_id는 null로). 감사 `booth.move`/`booth.update`.
  - `DELETE /booths/<id>` (own) → 204. 감사 `booth.delete`.
  - `POST /booths/<id>/toggle` (manage) → `{"is_active"}`
  - `POST /booths/batch-status {booth_ids, is_active}` — 모든 부스 own, 하나라도 아니면 404/403 그대로. 한 트랜잭션. 감사 `booth.batch_status` 1건. → `{"updated": n}`
  - `POST /booths/<id>/rotate-token` (own) → `{"manage_token"}`. 감사 `booth.rotate_token`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_booths.py`)

```python
def test_create_and_list(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    r = api(client, "POST", f"/api/admin/events/{ev}/booths",
            json={"name": " 페이스페인팅 ", "mode": "fcfs", "total_limit": 100})
    assert r.status_code == 201 and r.json["booth"]["name"] == "페이스페인팅"
    assert len(r.json["booth"]["manage_token"]) == 24

def test_invalid_hours(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    r = api(client, "POST", f"/api/admin/events/{ev}/booths",
            json={"name": "x", "mode": "time", "start_hour": 16, "end_hour": 11})
    assert r.status_code == 422 and "end_hour" in r.json["fields"]

def test_bulk_all_or_nothing(client, db, make_org, make_user, login_as, api, make_event):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    rows = [{"name": "a", "mode": "fcfs", "total_limit": 10}, {"name": "", "mode": "fcfs"}]
    r = api(client, "POST", f"/api/admin/events/{ev}/booths/bulk", json={"rows": rows})
    assert r.status_code == 422 and list(r.json["fields"]["rows"]) == ["1"]
    assert db.execute("SELECT COUNT(*) FROM booths").fetchone()[0] == 0

def test_linked_org_can_manage_not_own(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    host, linked = make_org(), make_org(); ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", linked, "t")).lastrowid
    b = make_booth(ev, partner_id=pid); make_user("kim", org_id=linked); login_as(client, "kim")
    assert api(client, "POST", f"/api/admin/booths/{b}/toggle").status_code == 200
    assert api(client, "PATCH", f"/api/admin/booths/{b}", json={"total_limit": 5}).status_code == 200
    assert api(client, "DELETE", f"/api/admin/booths/{b}").status_code == 403
    assert api(client, "POST", f"/api/admin/booths/{b}/rotate-token").status_code == 403
    assert api(client, "POST", "/api/admin/booths/batch-status", json={"booth_ids": [b], "is_active": False}).status_code == 403

def test_move_booth_clears_partner(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    e1, e2 = make_event(org), make_event(org)
    pid = db.execute("INSERT INTO partners (event_id,name,access_token,created_at) VALUES (?,?,?,0)", (e1, "p", "t")).lastrowid
    b = make_booth(e1, partner_id=pid)
    r = api(client, "PATCH", f"/api/admin/booths/{b}", json={"event_id": e2})
    assert r.json["booth"]["event_id"] == e2 and r.json["booth"]["partner_id"] is None

def test_settings_change_keeps_existing_reservations(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, mode="time", start_hour=11, end_hour=12, slots_per_hour=3, limit_per_slot=5)
    make_reservation(b, "가", slot="12시 C타임")
    r = api(client, "PATCH", f"/api/admin/booths/{b}", json={"slots_per_hour": 2, "end_hour": 11})
    assert r.status_code == 200 and r.json["booth"]["count"] == 1
    labels = [s["label"] for s in r.json["booth"]["availability"]["slots"]]
    assert labels == ["11시 A타임", "11시 B타임"]
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_booths.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 부스 관리 API 추가`

---

### Task 10: 명단·실적·내보내기 (`booth_ops` 나머지 + 관리 라우트)

**Files:**
- Modify: `app/booth_ops.py`, `app/routes/admin_booths.py`
- Test: `tests/test_reservations_admin.py`

**Interfaces:**
- Produces (`booth_ops`):
  - `AGE_GROUPS`, `GENDERS` 상수(Global Constraints 값)
  - `reservation_json(r: Row) -> dict` — `{id, booth_id, name, gender, age_group, code, slot, status, created_at}`
  - `list_reservations(booth_id: int) -> list[dict]` — 정렬: 슬롯 라벨의 시(숫자) 오름차순 → 라벨 문자열 → id. `'선착순'`은 id순.
  - `set_status(p, booth: Row, rid: int, status: str) -> dict` — 신청이 그 부스 소속이 아니면 404. 허용 값 4개. `waiting → normal`일 때 슬롯이 가득 차 있으면 응답에 `"over_capacity": true`(막지 않음). 감사 없음.
  - `delete_reservation(p, booth, rid) -> None` — 감사 `reservation.delete`(summary에 이름·슬롯).
  - `clear_reservations(p, booth) -> int` — 감사 `reservation.clear`(detail에 삭제 건수).
  - `stats(booth_ids: list[int], mode: Literal["simple","strict"]) -> dict` → `{"rows": [{"age_group","male","female","total"} × 6], "male", "female", "total"}` — 스펙 4절 집계 규칙.
  - `export(p, booth_ids: list[int]) -> dict` → `{"booths": [{"booth_id","event_name","booth_name","rows":[reservation_json 순서 그대로, id·booth_id 제외]}]}`. 감사 `export` 1건(org_id는 첫 부스의 기관, detail에 부스 id 목록).
  - `parse_booth_ids(body) -> list[int]` — 1~500개 정수, 중복 제거. 아니면 422.
- Produces (라우트, 모두 manage):
  - `GET /api/admin/booths/<id>/reservations` → `{"reservations": [...]}`
  - `PATCH /api/admin/reservations/<rid> {status}` → `{"reservation", "over_capacity"?}` (신청의 부스로 require_booth)
  - `DELETE /api/admin/reservations/<rid>` → 204
  - `DELETE /api/admin/booths/<id>/reservations` → `{"deleted": n}`
  - `POST /api/admin/stats {booth_ids, mode}` → stats (각 부스 require_booth)
  - `POST /api/admin/export {booth_ids}` → export

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_reservations_admin.py`)

```python
def test_stats_simple_and_strict(app, make_org, make_event, make_booth, make_reservation):
    from app.booth_ops import stats
    ev = make_event(make_org()); b1, b2 = make_booth(ev), make_booth(ev)
    make_reservation(b1, "가", gender="남", age_group="9~13세")
    make_reservation(b2, "가", gender="남", age_group="9~13세")          # 같은 사람
    make_reservation(b2, "나", gender="여", age_group="0~8세", status="noshow")
    with app.app_context():
        s = stats([b1, b2], "simple"); t = stats([b1, b2], "strict")
    assert s["total"] == 2 and s["male"] == 2 and s["female"] == 0
    assert t["total"] == 1
    assert [r["age_group"] for r in s["rows"]] == ["0~8세", "9~13세", "14~16세", "17~19세", "20~24세", "24세 이상"]

def test_noshow_frees_seat_and_waiting_promotion_warns(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, mode="fcfs", total_limit=1, use_waitlist=1)
    r1 = make_reservation(b, "가", slot="선착순"); r2 = make_reservation(b, "나", slot="선착순", status="waiting")
    assert api(client, "PATCH", f"/api/admin/reservations/{r2}", json={"status": "normal"}).json["over_capacity"] is True
    api(client, "PATCH", f"/api/admin/reservations/{r1}", json={"status": "noshow"})
    booth = api(client, "GET", f"/api/admin/booths/{b}").json["booth"]
    assert booth["availability"]["remaining"] == 0     # 나(normal) 1명 점유

def test_reservation_of_other_booth_404(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    a, b = make_org(), make_org(); make_user("hong", org_id=a); login_as(client, "hong")
    foreign = make_reservation(make_booth(make_event(b)), "가")
    assert api(client, "PATCH", f"/api/admin/reservations/{foreign}", json={"status": "noshow"}).status_code == 404
    assert api(client, "DELETE", f"/api/admin/reservations/{foreign}").status_code == 404

def test_clear_and_export_audited(client, db, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong"); ev = make_event(org)
    b = make_booth(ev, name="페인팅"); make_reservation(b, "가"); make_reservation(b, "나", code="5678")
    ex = api(client, "POST", "/api/admin/export", json={"booth_ids": [b]}).json
    assert ex["booths"][0]["booth_name"] == "페인팅" and len(ex["booths"][0]["rows"]) == 2
    assert api(client, "DELETE", f"/api/admin/booths/{b}/reservations").json == {"deleted": 2}
    actions = [r[0] for r in db.execute("SELECT action FROM audit_log ORDER BY id")]
    assert "export" in actions and "reservation.clear" in actions

def test_list_order(app, make_org, make_event, make_booth, make_reservation):
    from app.booth_ops import list_reservations
    b = make_booth(make_event(make_org()), mode="time", start_hour=9, end_hour=11, slots_per_hour=2)
    for name, slot in [("c", "11시 A타임"), ("a", "9시 B타임"), ("b", "9시 A타임")]:
        make_reservation(b, name, slot=slot)
    with app.app_context():
        assert [r["name"] for r in list_reservations(b)] == ["b", "a", "c"]
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_reservations_admin.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 명단·실적·내보내기 API 추가`

---

### Task 11: 링크 API (부스 링크, 실적 링크)

**Files:**
- Create: `app/routes/links.py`
- Test: `tests/test_links.py`

**Interfaces:**
- Consumes: `resolve_booth_link`, `resolve_partner_link`, `require_booth(..., p=principal)`, `booth_ops.*`
- Produces (prefix `/api/link`, CSRF 없음):
  - `GET /booth/<t>` → `{"booth": booth_json, "org": {name, slug}, "reservations": [...], "stats": stats([id],"simple")}`
  - `PATCH /booth/<t>` → `{"booth"}` — `SETTING_FIELDS`만. 그 밖의 키(`event_id`, `partner_id`)가 있으면 `403 forbidden`.
  - `POST /booth/<t>/toggle`, `PATCH/DELETE /booth/<t>/reservations/<rid>`, `DELETE /booth/<t>/reservations`, `GET /booth/<t>/export` — 관리 라우트와 같은 응답.
  - `GET /partner/<t>` → `{"partner": {id, name}, "event": {id, name}, "org": {name, slug}, "booths": [booth_json...]}`
  - `POST /partner/<t>/stats {booth_ids?, mode}` · `POST /partner/<t>/export {booth_ids?}` — booth_ids 생략 시 배정 부스 전체. 범위 밖 부스가 섞이면 404.
  - `GET|PATCH /partner/<t>/booths/<id>`, `POST /partner/<t>/booths/<id>/toggle`, `GET /partner/<t>/booths/<id>/reservations`, `PATCH|DELETE /partner/<t>/booths/<id>/reservations/<rid>`, `DELETE /partner/<t>/booths/<id>/reservations` — 부스 링크와 같은 동작, 배정 부스만.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_links.py`)

```python
def _setup(db, make_org, make_event, make_booth):
    host, linked = make_org(), make_org(); ev = make_event(host)
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", linked, "PTOKEN")).lastrowid
    mine, free = make_booth(ev, partner_id=pid, name="내 부스"), make_booth(ev, name="남의 부스")
    tok = lambda b: db.execute("SELECT manage_token FROM booths WHERE id=?", (b,)).fetchone()[0]
    return dict(host=host, linked=linked, ev=ev, pid=pid, mine=mine, free=free, tok=tok)

def test_booth_link_scope(client, db, make_org, make_event, make_booth, make_reservation):
    s = _setup(db, make_org, make_event, make_booth)
    t = s["tok"](s["free"])
    assert client.get(f"/api/link/booth/{t}").json["booth"]["name"] == "남의 부스"
    assert client.post(f"/api/link/booth/{t}/toggle").json["is_active"] is False
    assert client.patch(f"/api/link/booth/{t}", json={"event_id": s["ev"]}).status_code == 403
    foreign = make_reservation(s["mine"], "가")
    assert client.delete(f"/api/link/booth/{t}/reservations/{foreign}").status_code == 404

def test_partner_link_scope(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    g = client.get("/api/link/partner/PTOKEN").json
    assert [b["name"] for b in g["booths"]] == ["내 부스"]
    assert client.post(f"/api/link/partner/PTOKEN/booths/{s['mine']}/toggle").status_code == 200
    assert client.post(f"/api/link/partner/PTOKEN/booths/{s['free']}/toggle").status_code == 404
    assert client.post("/api/link/partner/PTOKEN/stats", json={"booth_ids": [s["free"]], "mode": "simple"}).status_code == 404
    assert client.post("/api/link/partner/PTOKEN/stats", json={"mode": "simple"}).status_code == 200

def test_link_dies_after_rotate_delete_or_org_deactivate(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    t = s["tok"](s["free"])
    db.execute("UPDATE booths SET manage_token='NEW' WHERE id=?", (s["free"],))
    assert client.get(f"/api/link/booth/{t}").status_code == 404
    db.execute("DELETE FROM partners WHERE id=?", (s["pid"],))
    assert client.get("/api/link/partner/PTOKEN").status_code == 404
    db.execute("UPDATE organizations SET is_active=0 WHERE id=?", (s["host"],))
    assert client.get("/api/link/booth/NEW").status_code == 404

def test_link_actions_audited_with_label(client, db, make_org, make_event, make_booth):
    s = _setup(db, make_org, make_event, make_booth)
    client.post(f"/api/link/booth/{s['tok'](s['free'])}/toggle")
    row = db.execute("SELECT actor, actor_label, org_id FROM audit_log WHERE action='booth.toggle'").fetchone()
    assert tuple(row) == ("booth_link", "부스 링크: 남의 부스", s["host"])

def test_linked_org_loses_access_after_unlink(client, db, make_org, make_event, make_booth, make_user, login_as, api):
    s = _setup(db, make_org, make_event, make_booth)
    make_user("kim", org_id=s["linked"]); login_as(client, "kim")
    assert api(client, "GET", f"/api/admin/booths/{s['mine']}").status_code == 200
    db.execute("UPDATE partners SET linked_org_id=NULL WHERE id=?", (s["pid"],))
    assert api(client, "GET", f"/api/admin/booths/{s['mine']}").status_code == 404
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_links.py -q`
- [ ] **Step 3: 구현** — 각 라우트는 `resolve_*_link`로 principal을 얻고 `require_booth(id, "manage", p)` 후 `booth_ops` 함수를 부른다. 관리 라우트와 코드를 공유한다.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 부스 링크·실적 링크 API 추가`

---

### Task 12: 방문객 API

**Files:**
- Create: `app/routes/public.py`
- Test: `tests/test_public.py`

**Interfaces:**
- Consumes: `capacity.*`, `ratelimit.guard`, `db.tx`
- Produces (prefix `/api/public`):
  - `normalize_name(s) -> str`(앞뒤 공백 제거, 1~50자), `normalize_code(s) -> str`(`[0-9]`만 남김(ASCII), 정확히 4자리 아니면 `ValidationError({"code": ...})`) — `app/routes/public.py` 모듈 함수.
  - `GET /orgs/<slug>` → 스펙 7.1 형식. 비활성 기관·없는 slug는 404. `is_active=1` 부스만, 부스가 없는 행사 제외. 행사 최신순, 부스 이름순.
  - `GET /orgs/<slug>/booths/<id>` → `{"org": {name, slug}, "event": {id, name}, "booth": {id, name, mode, use_waitlist, is_active}, "availability": ...}` — 비활성 부스도 반환(화면에서 "마감" 표시).
  - `POST /booths/<id>/reservations {name, gender, age_group, code, slot?}` → `201 {"id", "slot", "status"}`. `guard(f"reserve:{ip}", 30, 60)`. 하나의 `tx()` 안에서: 부스·기관 활성 확인 → 중복(`409 duplicate`) → `occupancy` → `decide_status` → 삽입.
  - `POST /booths/<id>/lookup {name, code}` → `{"name","slot","status","booth_name"}` 또는 404. `guard(f"lookup:{ip}", 20, 900)`.
  - `POST /booths/<id>/cancel {name, code}` → 204. 같은 제한 키. `noshow`·`completed`는 `409 not_cancellable`.
  - `GET /booths/<id>/home` → `{"slug"}`(비활성 기관 404).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_public.py`)

```python
def test_org_listing_hides_inactive(client, make_org, make_event, make_booth):
    org = make_org(slug="nareum"); ev = make_event(org, name="어울마당")
    make_booth(ev, name="열림"); make_booth(ev, name="닫힘", is_active=0); make_event(org, name="빈 행사")
    j = client.get("/api/public/orgs/nareum").json
    assert [e["name"] for e in j["events"]] == ["어울마당"]
    assert [b["name"] for b in j["events"][0]["booths"]] == ["열림"]
    assert client.get("/api/public/orgs/nope").status_code == 404

def test_booth_must_belong_to_slug(client, make_org, make_event, make_booth):
    make_org(slug="nareum"); other = make_org(slug="gmyouth")
    b = make_booth(make_event(other))
    assert client.get(f"/api/public/orgs/nareum/booths/{b}").status_code == 404

def test_reserve_normalizes_and_rejects_duplicate(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=10)
    body = {"name": " 홍길동 ", "gender": "남", "age_group": "9~13세", "code": "12-34"}
    r = client.post(f"/api/public/booths/{b}/reservations", json=body)
    assert r.status_code == 201 and r.json["slot"] == "선착순" and r.json["status"] == "normal"
    assert client.post(f"/api/public/booths/{b}/reservations", json={**body, "name": "홍길동"}).json["error"] == "duplicate"
    assert client.post(f"/api/public/booths/{b}/reservations", json={**body, "code": "１２３４"}).status_code == 422

def test_lookup_and_cancel_only_own(client, make_org, make_event, make_booth, make_reservation):
    b = make_booth(make_event(make_org()))
    make_reservation(b, "홍길동", code="1234"); make_reservation(b, "김철수", code="5678", status="completed")
    r = client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동 ", "code": "1234"})
    assert r.status_code == 200 and set(r.json) == {"name", "slot", "status", "booth_name"}
    assert client.post(f"/api/public/booths/{b}/lookup", json={"name": "홍길동", "code": "9999"}).status_code == 404
    assert client.post(f"/api/public/booths/{b}/cancel", json={"name": "김철수", "code": "5678"}).json["error"] == "not_cancellable"
    assert client.post(f"/api/public/booths/{b}/cancel", json={"name": "홍길동", "code": "1234"}).status_code == 204

def test_lookup_rate_limit(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org()))
    codes = [client.post(f"/api/public/booths/{b}/lookup", json={"name": "x", "code": "0000"}).status_code for _ in range(21)]
    assert codes[:20] == [404] * 20 and codes[20] == 429

def test_concurrent_reservations_never_exceed(app, make_org, make_event, make_booth, db):
    import threading
    b = make_booth(make_event(make_org()), mode="fcfs", total_limit=5)
    results = []
    def go(i):
        c = app.test_client()
        r = c.post(f"/api/public/booths/{b}/reservations",
                   json={"name": f"p{i}", "gender": "여", "age_group": "0~8세", "code": f"{i:04d}"},
                   environ_base={"REMOTE_ADDR": f"10.0.0.{i}"})
        results.append(r.status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(201) == 5 and results.count(409) == 15
    assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 5

def test_home_redirect_slug(client, make_org, make_event, make_booth):
    b = make_booth(make_event(make_org(slug="gmyouth")))
    assert client.get(f"/api/public/booths/{b}/home").json == {"slug": "gmyouth"}
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_public.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 방문객 API 추가 (서버 대조 조회·취소, 정원 잠금)`

---

### Task 13: 대시보드 요약, 감사 로그 열람

**Files:**
- Modify: `app/routes/admin_booths.py`(overview, audit), `app/routes/super.py`(audit)
- Test: `tests/test_overview_audit.py`

**Interfaces:**
- Consumes: `require_org`, `booth_ops.booth_json`, `audit.list_audit`, `timeutil.kst_date`
- Produces:
  - `GET /api/admin/orgs/<org>/overview` →
    ```
    {"org": {id,name,slug},
     "owned": [{"event": {id,name}, "booths": [booth_json...]}],          # 행사 최신순
     "linked": [{"partner": {id,name}, "event": {id,name}, "host": {id,name}, "booths": [...]}],
     "totals": {"today": int, "total": int, "active_booths": int, "booths": int}}
    ```
    `linked`는 활성 주최 기관만. totals는 owned + linked 부스 전체, `total`·`today`는 `noshow` 제외, `today`는 `kst_date(created_at) == kst_date(now())`.
  - `GET /api/admin/orgs/<org>/audit?before&limit&action` → `list_audit(org_id=org, ...)`
  - `GET /api/admin/super/audit?before&limit&action&org_id` → `list_audit(org_id=<org_id 또는 None>, ...)`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_overview_includes_linked(client, db, make_org, make_user, login_as, api, make_event, make_booth, make_reservation):
    host, me = make_org(name="재단"), make_org(name="나름")
    own_ev = make_event(me, name="어울마당"); make_booth(own_ev, name="내 행사 부스")
    ev = make_event(host, name="난장")
    pid = db.execute("INSERT INTO partners (event_id,name,linked_org_id,access_token,created_at) VALUES (?,?,?,?,0)",
                     (ev, "나름", me, "t")).lastrowid
    b = make_booth(ev, partner_id=pid, name="난장 나름 부스"); make_booth(ev, name="재단 부스")
    make_reservation(b, "가"); make_reservation(b, "나", code="5678", status="noshow")
    make_user("kim", org_id=me); login_as(client, "kim")
    o = api(client, "GET", f"/api/admin/orgs/{me}/overview").json
    assert [g["event"]["name"] for g in o["owned"]] == ["어울마당"]
    assert o["linked"][0]["host"]["name"] == "재단" and [x["name"] for x in o["linked"][0]["booths"]] == ["난장 나름 부스"]
    assert o["totals"]["booths"] == 2 and o["totals"]["total"] == 1

def test_today_uses_kst(client, make_org, make_user, login_as, api, make_event, make_booth, make_reservation, monkeypatch):
    import datetime as dt, app.timeutil as tu
    org = make_org(); make_user("kim", org_id=org); login_as(client, "kim"); b = make_booth(make_event(org))
    utc = lambda *a: dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp()
    make_reservation(b, "어제", created_at=utc(2026, 5, 4, 14, 59))     # KST 5/4 23:59
    make_reservation(b, "오늘", code="5678", created_at=utc(2026, 5, 4, 15, 1))  # KST 5/5 00:01
    monkeypatch.setattr(tu, "now", lambda: utc(2026, 5, 5, 3, 0))
    assert api(client, "GET", f"/api/admin/orgs/{org}/overview").json["totals"]["today"] == 1

def test_audit_visibility(client, db, make_org, make_user, login_as, api, make_event, make_booth):
    a, b = make_org(), make_org()
    make_user("hong", org_id=a); make_user("kim", org_id=b); make_user("root", superadmin=True)
    login_as(client, "hong")
    ev = api(client, "POST", f"/api/admin/orgs/{a}/events", json={"name": "A행사"}).json["event"]["id"]
    login_as(client, "kim")
    own = api(client, "GET", f"/api/admin/orgs/{b}/audit").json["items"]
    assert [i["action"] for i in own] == ["auth.login"]           # A 기관의 event.create는 안 보인다
    assert api(client, "GET", f"/api/admin/orgs/{a}/audit").status_code == 404
    login_as(client, "root")
    items = api(client, "GET", f"/api/admin/super/audit?action=event.").json["items"]
    assert [i["summary"] for i in items] and items[0]["actor_label"] == "hong(hong)"
```

  (`timeutil.now`를 쓰는 곳은 `from . import timeutil` 후 `timeutil.now()`로 불러야 monkeypatch가 먹는다. Task 13 구현 시 overview에서 이 방식으로 호출한다.)

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_overview_audit.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 대시보드 요약과 감사 로그 열람 API 추가`

---

### Task 14: 격리 매트릭스, CORS, 보안 헤더, 정리 스레드

**Files:**
- Modify: `app/__init__.py`, `app/config.py`
- Create: `app/housekeeping.py`
- Test: `tests/test_isolation.py`, `tests/test_ops.py`

**Interfaces:**
- Produces:
  - CORS: `flask_cors.CORS(app, resources={r"/api/*": {"origins": CORS_ORIGINS}}, supports_credentials=True, allow_headers=["Content-Type", "X-CSRF-Token"], methods=["GET","POST","PATCH","DELETE","OPTIONS"])`
  - 보안 헤더: 스펙 7.6(walnutphoto `_security_headers`에서 `/p/` 조건 제거).
  - `housekeeping.prune() -> int` — 24시간 지난 `rate_events`, 만료·유휴 세션 삭제. 팩토리에서 `CLEANUP_INTERVAL > 0`이고 테스트가 아니면 데몬 스레드로 주기 실행.
  - `tests/test_isolation.py`: walnutphoto `tests/test_isolation.py` 구조를 따른다.
    - 기관 A(계정 `hong`)와 기관 B(행사·운영 단체·부스·신청·계정 각 1개) 준비.
    - `CASES: dict[str, tuple[str, Callable[[ids], str]]]` — `/api/admin/` 아래 auth·super를 제외한 모든 라우트의 `endpoint` 이름 → (메서드, B의 자원으로 채운 URL).
    - `test_other_org_is_404`: `hong`으로 각 케이스 호출 → 404 (`POST /api/admin/stats`, `/export`, `/booths/batch-status`는 body에 B 부스 id).
    - `test_cases_cover_every_admin_endpoint`: `app.url_map`에서 규칙이 `/api/admin/`으로 시작하고 `/auth/`, `/super/`가 아닌 endpoint 집합 == `CASES` 키 집합.
    - `test_every_super_endpoint_forbids_owner`: `/api/admin/super/` 규칙 전부 `hong`으로 → 403.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_ops.py
def test_cors_allows_frontend_with_credentials(client):
    r = client.options("/api/public/orgs/x", headers={
        "Origin": "https://nrbooth.team-cluster.kr", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert r.headers["Access-Control-Allow-Origin"] == "https://nrbooth.team-cluster.kr"
    assert r.headers["Access-Control-Allow-Credentials"] == "true"

def test_cors_rejects_unknown_origin(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers

def test_security_headers(client):
    h = client.get("/api/health").headers
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store" and h["Referrer-Policy"] == "no-referrer"

def test_prune(app, db):
    from app.housekeeping import prune
    db.execute("INSERT INTO rate_events (key, at) VALUES ('old', 0)")
    with app.app_context():
        assert prune() >= 1
```

  격리 테스트는 위 Interfaces 설명대로 작성한다.

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_isolation.py tests/test_ops.py -q`. `test_cases_cover_every_admin_endpoint`가 누락 endpoint 이름을 보여주면 CASES에 채운다.
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q` → 전부 PASS.
- [ ] **Step 5: 커밋** — `[test] 기관 격리 매트릭스와 운영 설정 추가`

---

### Task 15: 기존 데이터 이관 스크립트

**Files:**
- Create: `scripts/__init__.py`(빈 파일), `scripts/migrate_legacy.py`
- Test: `tests/test_migrate_legacy.py`

**Interfaces:**
- Consumes: `app.db.migrate`, `app.security.link_token`
- Produces:
  - `HOST_EVENT = "2026년 광명시 청소년의 달 기념행사 오월의 난장"`, `DEFAULT_EVENT = "기본 행사(미분류)"`
  - `ORGS = [("나름청소년활동센터", "nareum"), ("광명시청소년재단", "gmyouth")]`
  - `class MigrationError(Exception)`
  - `run(legacy: Path, out: Path, *, now_ts: float | None = None) -> dict` — 반환 보고서:
    `{"orgs": {"nareum": {"events": n, "booths": n, "reservations": n}, "gmyouth": {...}}, "dropped": {"events": n, "booths": n, "reservations": n}, "status": {"normal": n, "completed": n, "noshow": n, "waiting": n}, "slot_renamed": n}`
  - 동작은 스펙 8절. 기존 DB는 `mode=ro` URI로 연다. 새 DB 쓰기는 하나의 트랜잭션. 끝에 새 DB 건수와 (기존 − 버린 것)을 대조해 다르면 `MigrationError`(새 파일 삭제).
  - `created_at` 문자열 파싱: SQLAlchemy 기본값 `CURRENT_TIMESTAMP`는 UTC `"YYYY-MM-DD HH:MM:SS"`, `fix_db.py`가 넣은 값은 `"2026-05-23 00:00:00"` — 모두 UTC로 해석. 파싱 실패·NULL은 부스 `created_at`, 그것도 없으면 `now_ts`.
  - CLI: `python scripts/migrate_legacy.py --legacy PATH --out PATH` → 보고서를 표로 출력, 오류 시 종료 코드 1.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_migrate_legacy.py`)

  테스트 픽스처 `legacy_db(tmp_path)`는 `legacy/app.py`의 모델과 같은 컬럼으로 `event`, `booth`, `reservation` 테이블을 `sqlite3`로 만들고 다음을 넣는다.
  - 이벤트 `1 기본 행사(미분류)`, `2 어린이날 나름`, `3 HOST_EVENT`
  - 부스 `10(ev1)`, `20(ev2, mode time)`, `30(ev3, fcfs)`, `31(ev3, event_id NULL)`
  - 신청: 부스 10에 1건, 부스 20에 2건(`normal`, `noshow`), 부스 30에 2건(`time='선착순 접수'`, `status NULL`), 부스 31에 1건

```python
def test_migrates_and_maps(tmp_path, legacy_db):
    from scripts.migrate_legacy import run
    out = tmp_path / "app.db"
    rep = run(legacy_db, out, now_ts=1.0)
    assert rep["orgs"]["nareum"] == {"events": 1, "booths": 1, "reservations": 2}
    assert rep["orgs"]["gmyouth"] == {"events": 1, "booths": 1, "reservations": 2}
    assert rep["dropped"] == {"events": 1, "booths": 2, "reservations": 2}
    assert rep["slot_renamed"] == 1
    import sqlite3; c = sqlite3.connect(out)
    assert c.execute("SELECT slot, status FROM reservations WHERE booth_id=30 ORDER BY id").fetchall()[0] == ("선착순", "normal")
    assert c.execute("SELECT id FROM booths ORDER BY id").fetchall() == [(20,), (30,)]
    assert all(len(t) == 24 for (t,) in c.execute("SELECT manage_token FROM booths"))
    assert c.execute("SELECT slug FROM organizations o JOIN events e ON e.org_id=o.id WHERE e.id=3").fetchone() == ("gmyouth",)

def test_refuses_existing_out(tmp_path, legacy_db):
    from scripts.migrate_legacy import run, MigrationError
    out = tmp_path / "app.db"; out.write_bytes(b"x")
    import pytest
    with pytest.raises(MigrationError): run(legacy_db, out)

def test_refuses_when_host_event_missing(tmp_path, legacy_db):
    import sqlite3, pytest
    from scripts.migrate_legacy import run, MigrationError
    sqlite3.connect(legacy_db).execute("UPDATE event SET name='다른 이름' WHERE id=3").connection.commit()
    with pytest.raises(MigrationError): run(legacy_db, tmp_path / "app.db")
    assert not (tmp_path / "app.db").exists()
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_migrate_legacy.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 로컬 사본으로 실제 실행** — `python scripts/migrate_legacy.py --legacy db/reservations.db --out data/app.db` → 오류 없이 보고서 출력. 기대값(로컬 사본 기준): gmyouth 행사 1·부스 37·신청 3,929, 버림 행사 1·부스 2·신청 4. 다르면 원인을 확인하고 보고한다. 생성된 `data/app.db`는 커밋하지 않는다(.gitignore).
- [ ] **Step 6: 커밋** — `[feat] 기존 데이터 이관 스크립트 추가`

---

### Task 16: 배포 구성 정리, 레거시 제거

**Files:**
- Create: `wsgi.py`(`app = create_app()`), `gunicorn.conf.py`(bind `0.0.0.0:5005`, workers 4, `preload_app = False`), `.env.example`(Config 항목 설명), `.dockerignore`(`data/`, `db/`, `tests/`, `.env`, `__pycache__`)
- Modify: `Dockerfile`, `README.md`
- Delete: `legacy/app.py`, `legacy/fix_db.py`

**Interfaces:** 없음 (배포 산출물)

- [ ] **Step 1: Dockerfile 교체** — `python:3.11-slim`, `pip install --no-cache-dir -r requirements.txt`(런타임만), `ENV DATA_DIR=/data`, `VOLUME /data`, `CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]`.
- [ ] **Step 2: README 작성** — 로컬 실행, 테스트, 환경변수, 슈퍼관리자 생성, **운영 전환 절차**(스펙 9절: 운영 DB 백업 → `docker run --rm -v ... python scripts/migrate_legacy.py --legacy /old/reservations.db --out /data/app.db` → 새 컨테이너 기동 → `flask create-superadmin` → 계정 발급).
- [ ] **Step 3: 레거시 삭제** — `git rm legacy/app.py legacy/fix_db.py`. `tests/test_migrate_legacy.py`가 `legacy/app.py`를 읽지 않는지 확인(픽스처가 스키마를 직접 만든다).
- [ ] **Step 4: 전체 검증** — `python -m pytest -q` → 전부 PASS. `docker build -t nr-booth-be .`(Docker가 있으면) 성공. 없으면 그 사실을 보고한다.
- [ ] **Step 5: 커밋** — `[chore] 배포 구성 정리 및 레거시 코드 제거`
