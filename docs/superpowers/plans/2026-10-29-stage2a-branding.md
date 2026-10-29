# 2-A 백엔드 추가분(로고·행사 안내 문구) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기관 로고·행사 로고 업로드와 공개 제공, 행사 안내 문구를 백엔드에 추가하고 관련 응답을 확장한다.

**Architecture:** 이미지는 `DATA_DIR/assets/<sha256>.<ext>`에 내용 주소로 저장하고 DB에는 파일 이름만 둔다. 업로드 검증·저장·참조 정리는 `app/assets.py` 한곳에서 하고, 라우트는 권한 확인 후 이 모듈을 부른다. 공개 제공은 `/api/public/assets/<name>` 하나로, 긴 캐시 헤더를 단다.

**Tech Stack:** 기존 그대로 — Python 3.11+, Flask 3.1, sqlite3, pytest. 새 의존성 없음(이미지 판별은 파일 시그니처로).

**Spec:** 프론트 저장소 `../NR_ChildDay2026/docs/superpowers/specs/2026-10-29-stage2-frontend-design.md` 7절(이 계획의 기준). API 기존 계약은 이 저장소 `docs/superpowers/specs/2026-10-29-multitenant-design.md` 7절.

## Global Constraints

- 작업 브랜치 `update`, 커밋 메시지 `[feat] …`/`[fix] …`/`[test] …`/`[docs] …` + 빈 줄 + 작성 모델의 Co-Authored-By 줄.
- 허용 형식: PNG(`\x89PNG\r\n\x1a\n`), JPEG(`\xff\xd8\xff`), WebP(`RIFF????WEBP`) — 파일 앞부분 시그니처로만 판별. 확장자는 판별 결과로 정한다(`png`/`jpg`/`webp`). SVG 및 그 밖의 형식 거부.
- 최대 크기 1MB(1,048,576바이트). 초과 시 `413 {"error":"file_too_large"}`.
- 자산 이름 형식: `^[0-9a-f]{64}\.(png|jpg|webp)$`. 그 외 이름은 404.
- `logo_url` 값: `/api/public/assets/<name>` 또는 `null`.
- 공개 자산 응답 헤더: `Cache-Control: public, max-age=31536000, immutable`, 형식에 맞는 `Content-Type`(`image/png`/`image/jpeg`/`image/webp`), `X-Content-Type-Options: nosniff`.
- `notice`: 문자열 앞뒤 공백 제거 후 0~500자, 빈 문자열은 `NULL`로 저장. 그 외 타입(null 제외)은 422.
- 업로드 필드 이름 `file`(multipart). 없거나 빈 파일이면 `422 {"fields":{"file": …}}`.
- 감사 로그: `org.logo`, `event.logo`(설정/삭제 구분은 detail `{"action": "set"|"delete"}`), `event.update`의 detail에 notice 변경 포함.
- 다른 기관 자원은 404(기존 격리 매트릭스에 새 라우트 포함).

## Review Focus

1. **같은 이미지를 기관 로고와 행사 로고에 함께 쓰고 한쪽만 삭제** — 다른 쪽이 계속 보여야 한다(파일 유지). → Task 1·2 테스트.
2. **multipart가 아니거나 `file` 필드가 없거나 0바이트** — 500이 아니라 422. → Task 2 테스트.
3. **1MB를 조금 넘는 파일과 아주 큰 요청 본문** — 둘 다 JSON `413 file_too_large`(HTML 오류 페이지 아님). → Task 2 테스트.
4. **자산 이름에 경로 조작(`..%2Fapp.db`, 대문자 hex, 다른 확장자)** — 404, 데이터 디렉터리 밖 파일 노출 없음. → Task 2 테스트.
5. **안내 문구가 공백만이거나 501자** — 공백만이면 `null`로 저장, 501자는 422. → Task 3 테스트.

---

## File Structure

```
migrations/002_branding.sql      organizations.logo, events.logo, events.notice
app/assets.py                    판별·저장·URL·참조 정리
app/routes/assets_admin.py       PUT/DELETE 로고(기관·행사) — 관리 블루프린트
app/routes/public.py             GET /api/public/assets/<name> 추가, 응답에 logo_url·notice
app/routes/admin_events.py       PATCH notice, 행사 JSON에 logo_url·notice
app/routes/auth.py               /auth/me org에 logo_url
app/access.py                    BOOTH_SQL에 org_logo, event_logo, event_notice
app/__init__.py                  MAX_CONTENT_LENGTH, 413 JSON 처리
tests/test_assets.py, tests/test_branding.py, tests/test_isolation.py(CASES 추가)
docs/superpowers/specs/2026-10-29-multitenant-design.md  7절 갱신
```

---

### Task 1: 스키마와 자산 모듈

**Files:**
- Create: `migrations/002_branding.sql`, `app/assets.py`
- Test: `tests/test_assets.py`

**Interfaces:**
- Produces (`app/assets.py`):
  - `MAX_BYTES = 1_048_576`
  - `NAME_RE = re.compile(r"[0-9a-f]{64}\.(png|jpg|webp)")` (fullmatch로 사용)
  - `CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}`
  - `sniff(data: bytes) -> str | None` — `"png"|"jpg"|"webp"` 또는 None
  - `store(data: bytes) -> str` — 크기 초과 `ApiError(413, "file_too_large")`, 형식 불명 `ValidationError({"file": "PNG, JPG, WebP 이미지만 올릴 수 있어요."})`; `DATA_DIR/assets/`에 `<sha256>.<ext>`로 저장(이미 있으면 다시 쓰지 않음), 이름 반환. 임시 파일에 쓴 뒤 `os.replace`.
  - `url(name: str | None) -> str | None` — `/api/public/assets/<name>` 또는 None
  - `path_for(name: str) -> Path | None` — 이름이 `NAME_RE`에 맞고 파일이 있으면 경로, 아니면 None
  - `release(name: str | None) -> None` — `organizations.logo`·`events.logo` 어디에서도 참조하지 않으면 파일 삭제. **트랜잭션 커밋 뒤에** 호출한다(get_db로 참조 조회).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_assets.py`)

```python
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
        with pytest.raises(ValidationError): store(SVG)
        with pytest.raises(ApiError) as e: store(PNG + b"\x00" * 1_048_576)
        assert e.value.status == 413 and e.value.code == "file_too_large"

def test_path_for_rejects_bad_names(app):
    from app.assets import path_for
    with app.app_context():
        for bad in ("../app.db", "A" * 64 + ".png", "a" * 64 + ".svg", "a" * 63 + ".png"):
            assert path_for(bad) is None

def test_release_keeps_shared_file(app, db, make_org, make_event):
    from app.assets import store, release, path_for
    org = make_org(); ev = make_event(org)
    with app.app_context():
        name = store(PNG)
    db.execute("UPDATE organizations SET logo=? WHERE id=?", (name, org))
    db.execute("UPDATE events SET logo=? WHERE id=?", (name, ev))
    db.execute("UPDATE organizations SET logo=NULL WHERE id=?", (org,))
    with app.app_context():
        release(name); assert path_for(name) is not None      # 행사가 아직 참조
    db.execute("UPDATE events SET logo=NULL WHERE id=?", (ev,))
    with app.app_context():
        release(name); assert path_for(name) is None
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_assets.py -q` → import/컬럼 오류.
- [ ] **Step 3: 구현** — `002_branding.sql`은 스펙 7.1의 세 `ALTER TABLE` 그대로. `app/assets.py`는 위 시그니처대로.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q` → 전부 PASS.
- [ ] **Step 5: 커밋** — `[feat] 로고 자산 저장 모듈과 스키마 추가`

---

### Task 2: 로고 업로드·삭제 API와 공개 제공

**Files:**
- Create: `app/routes/assets_admin.py`
- Modify: `app/routes/public.py`(자산 제공 라우트), `app/__init__.py`(본문 크기 제한·413), `app/routes/__init__.py`(블루프린트 등록), `tests/test_isolation.py`(CASES)
- Test: `tests/test_branding.py`

**Interfaces:**
- Consumes: Task 1 `store`, `url`, `path_for`, `release`, `CONTENT_TYPES`; 기존 `require_org(org_id)`, `require_event(event_id)`, `login_required`, `audit`, `current_principal`, `tx`.
- Produces:
  - `PUT /api/admin/orgs/<org>/logo` → `{"logo_url": ...}`; `DELETE` → 204 (이미 없으면 그대로 204)
  - `PUT /api/admin/events/<id>/logo` → `{"logo_url": ...}`; `DELETE` → 204
  - `GET /api/public/assets/<name>` → 이미지(헤더는 Global Constraints) 또는 404
  - `app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024`; 413 오류를 `/api/*`에서 `{"error":"file_too_large"}` JSON으로.
- 흐름: 파일 읽기 → `store()`(tx 밖) → tx 안에서 이전 이름 읽고 UPDATE + audit → 커밋 뒤 `release(이전 이름)`. 업로드는 CSRF 대상(`/api/admin/` 변경 요청)이다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_branding.py`) — `PNG`/`JPG`/`SVG` 상수는 `tests/test_assets.py`에서 import.

```python
import io
from tests.test_assets import PNG, JPG, SVG

def up(api, client, url, data, field="file"):
    return client.open(url, method="PUT", data={field: (io.BytesIO(data), "x.png")},
                       headers={"X-CSRF-Token": client.csrf}, content_type="multipart/form-data")

def test_org_logo_roundtrip(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    r = up(api, client, f"/api/admin/orgs/{org}/logo", PNG)
    assert r.status_code == 200 and r.json["logo_url"].startswith("/api/public/assets/")
    g = client.get(r.json["logo_url"])
    assert g.status_code == 200 and g.data == PNG and g.headers["Content-Type"] == "image/png"
    assert g.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert api(client, "DELETE", f"/api/admin/orgs/{org}/logo").status_code == 204
    assert client.get(r.json["logo_url"]).status_code == 404

def test_shared_image_survives_one_delete(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    u = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    assert up(api, client, f"/api/admin/events/{ev}/logo", PNG).json["logo_url"] == u
    api(client, "DELETE", f"/api/admin/orgs/{org}/logo")
    assert client.get(u).status_code == 200

def test_replace_releases_old(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    old = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    up(api, client, f"/api/admin/orgs/{org}/logo", JPG)
    assert client.get(old).status_code == 404

def test_bad_uploads(client, make_org, make_user, login_as, api):
    org = make_org(); make_user("hong", org_id=org); login_as(client, "hong")
    u = f"/api/admin/orgs/{org}/logo"
    assert up(api, client, u, SVG).status_code == 422
    assert up(api, client, u, b"").status_code == 422
    assert up(api, client, u, PNG, field="other").status_code == 422
    assert api(client, "PUT", u, json={"file": "x"}).status_code == 422
    assert up(api, client, u, PNG + b"\x00" * 1_048_576).json == {"error": "file_too_large"}
    big = up(api, client, u, PNG + b"\x00" * (3 * 1024 * 1024))
    assert big.status_code == 413 and big.json == {"error": "file_too_large"}

def test_asset_name_traversal(client):
    for p in ("..%2Fapp.db", "A" * 64 + ".png", "a" * 64 + ".svg"):
        assert client.get(f"/api/public/assets/{p}").status_code == 404

def test_logo_audited_and_isolated(client, db, make_org, make_user, login_as, api, make_event):
    a, b = make_org(), make_org(); evb = make_event(b); make_user("hong", org_id=a); login_as(client, "hong")
    assert up(api, client, f"/api/admin/orgs/{b}/logo", PNG).status_code == 404
    assert up(api, client, f"/api/admin/events/{evb}/logo", PNG).status_code == 404
    up(api, client, f"/api/admin/orgs/{a}/logo", PNG)
    row = db.execute("SELECT action, org_id FROM audit_log WHERE action='org.logo'").fetchone()
    assert tuple(row) == ("org.logo", a)
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_branding.py -q`
- [ ] **Step 3: 구현** — 위 Interfaces대로. `tests/test_isolation.py`의 `CASES`에 새 관리 엔드포인트 4개(PUT은 multipart 본문이 필요하므로 케이스의 본문 생성 방식을 확장하거나 빈 JSON으로 호출해 404가 권한 단계에서 나는지 확인 — 권한 확인을 본문 파싱보다 먼저 한다).
- [ ] **Step 4: 통과 확인** — `python -m pytest -q`
- [ ] **Step 5: 커밋** — `[feat] 기관·행사 로고 업로드와 공개 제공 추가`

---

### Task 3: 행사 안내 문구와 응답 확장, 스펙 갱신

**Files:**
- Modify: `app/routes/admin_events.py`(PATCH notice, `_event_out`), `app/routes/public.py`(org/booth 응답), `app/routes/auth.py`(`/me`), `app/access.py`(`BOOTH_SQL`), `docs/superpowers/specs/2026-10-29-multitenant-design.md`(7절)
- Test: `tests/test_branding.py`(추가)

**Interfaces:**
- Consumes: Task 1 `assets.url`.
- Produces:
  - `PATCH /api/admin/events/<id>` 본문은 `name`과 `notice` 모두 선택(둘 다 없으면 422). notice 규칙은 Global Constraints. 감사 `event.update` detail에 바뀐 항목만 `{from, to}`.
  - 관리 행사 JSON(`_event_out`)에 `logo_url`, `notice`.
  - 방문객 `GET /orgs/<slug>`: `org.logo_url`; 각 event에 `logo_url`, `notice`.
  - 방문객 `GET /orgs/<slug>/booths/<id>`: `org.logo_url`, `event.logo_url`, `event.notice`.
  - `/auth/me`의 `org`에 `logo_url`.
  - `BOOTH_SQL`에 `o.logo AS org_logo, e.logo AS event_logo, e.notice AS event_notice` 추가(기존 키는 그대로).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_branding.py`에 추가)

```python
def test_notice_rules(client, make_org, make_user, login_as, api, make_event):
    org = make_org(); ev = make_event(org); make_user("hong", org_id=org); login_as(client, "hong")
    r = api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "  시작 5분 뒤 미도착 시 취소  "})
    assert r.json["event"]["notice"] == "시작 5분 뒤 미도착 시 취소"
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "   "}).json["event"]["notice"] is None
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "가" * 501}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": 3}).status_code == 422
    assert api(client, "PATCH", f"/api/admin/events/{ev}", json={}).status_code == 422
    r = api(client, "PATCH", f"/api/admin/events/{ev}", json={"name": "새 이름"})
    assert r.json["event"]["name"] == "새 이름" and r.json["event"]["notice"] is None

def test_public_payloads_carry_branding(client, make_org, make_user, login_as, api, make_event, make_booth):
    org = make_org(slug="nareum"); ev = make_event(org); b = make_booth(ev)
    make_user("hong", org_id=org); login_as(client, "hong")
    org_url = up(api, client, f"/api/admin/orgs/{org}/logo", PNG).json["logo_url"]
    ev_url = up(api, client, f"/api/admin/events/{ev}/logo", JPG).json["logo_url"]
    api(client, "PATCH", f"/api/admin/events/{ev}", json={"notice": "안내"})
    p = client.get("/api/public/orgs/nareum").json
    assert p["org"]["logo_url"] == org_url and p["events"][0]["logo_url"] == ev_url and p["events"][0]["notice"] == "안내"
    bp = client.get(f"/api/public/orgs/nareum/booths/{b}").json
    assert (bp["org"]["logo_url"], bp["event"]["logo_url"], bp["event"]["notice"]) == (org_url, ev_url, "안내")
    assert client.get("/api/admin/auth/me").json["org"]["logo_url"] == org_url

def test_payloads_null_without_branding(client, make_org, make_event, make_booth):
    org = make_org(slug="gmyouth"); b = make_booth(make_event(org))
    p = client.get("/api/public/orgs/gmyouth").json
    assert p["org"]["logo_url"] is None and p["events"][0]["logo_url"] is None and p["events"][0]["notice"] is None
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_branding.py -q`
- [ ] **Step 3: 구현** — 위 Interfaces대로. 스펙 7절: 7.1 응답 형식에 `logo_url`·`notice` 추가, 7.3 표에 로고 라우트 4개와 PATCH notice, 7.1에 `GET /assets/<name>` 행, 오류 코드 표에 `file_too_large`(413), 감사 로그 표에 `org.logo`·`event.logo`.
- [ ] **Step 4: 통과 확인** — `python -m pytest -q` → 전부 PASS.
- [ ] **Step 5: 커밋** — `[feat] 행사 안내 문구와 로고 응답 확장`
