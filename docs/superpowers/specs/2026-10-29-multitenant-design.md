# 체험부스 예약 시스템 멀티테넌트 전환 설계

- 작성일: 2026-10-29
- 대상 저장소: `NR_ChildDay2026-BE`(백엔드), `NR_ChildDay2026`(프론트엔드), 두 저장소 모두 `update` 브랜치
- 참고 구현: `../walnutphoto` (server/, admin/)

## 1. 배경과 목표

현재 시스템은 기관 구분 없이 모든 행사·부스·신청 명단을 누구나 조회·수정할 수 있다
(관리자 인증 미연결, 공개 API로 명단 노출·삭제 가능). 이를 다음과 같이 바꾼다.

1. 여러 기관(테넌트)이 각자 행사를 소유하고 운영한다. 예: 광명시청소년재단 → 오월의 난장,
   나름청소년활동센터 → NADISUM·날드론쥐·어울마당.
2. 한 행사에 다른 센터·외부 단체가 부스를 운영할 수 있고, 운영 주체별로 실적을 골라 받는다.
3. 부스 단위 관리는 로그인 없이 링크로 가능하다(운영 시작/마감, 설정 수정, 명단, 엑셀, 초기화).
4. 계정은 이메일 없이 아이디로 발급하고, 최초 로그인 시 비밀번호를 바꾼다.
5. 방문객 화면은 기관별 주소(`/nareum`, `/gmyouth`)로 구분한다.

### 작업 단계

| 단계 | 범위 | 배포 |
|---|---|---|
| 1 | 백엔드 재구성: 멀티테넌트, 인증, 링크 권한, 공개 API 보호, 이관 스크립트 | 2단계와 함께 |
| 2 | 프론트엔드: 로그인·계정·기관·링크 화면, 새 API 연동, 방문객 경로 변경 | 1단계와 함께 |
| 3 | 전체 디자인 개편(현재 톤 유지), 기관·행사별 꾸밈 요소(로고 등) | 이후 |

이 문서는 1단계를 상세히, 2·3단계는 1단계가 보장해야 할 범위만 정의한다.
2·3단계는 각자 별도 설계 문서를 둔다.

## 2. 현재 시스템 요약

- 백엔드: Flask + Flask-SQLAlchemy + SQLite, `app.py` 단일 파일, gunicorn 워커 4개, NAS Docker.
- 프론트: React 19 + Vite + Tailwind 4, JS. 방문객(`/`, `/reserve/:id`, `/check/:id`)과
  관리자(`/manage/*`: 대시보드, 행사관리, 부스관리, 부스 상세, 합계실적, 운영마감) 화면.
- 데이터: Event → Booth → Reservation. 로컬 사본 기준 행사 6, 부스 73, 신청 5,669.

### 해결 대상 문제

1. 관리자 인증 없음. 프론트 비밀번호 가드는 미연결이며 번들에 노출되는 방식.
2. `GET /api/booths/<id>/reservations`로 부스 전체 명단(이름·식별번호) 공개 조회 가능.
   방문객 확인 화면이 이 명단을 받아 브라우저에서 검색한다.
3. `DELETE /api/reservations/<id>`, `DELETE /api/clear-all` 공개.
4. 정원 계산이 노쇼·대기자를 포함하고, 화면마다 계산 로직이 중복된다.
   워커 4개 동시 신청 시 정원 초과 가능(트랜잭션 잠금 없음).
5. 대시보드가 부스 이름으로 신청을 매칭해 동명 부스가 섞이고, 합계실적은 부스 수만큼 API를 호출한다.
6. 선착순 슬롯 값 불일치(`'선착순'` / `'선착순 접수'`), 기본 행사 판별 불일치(`id===1` / `first()`).

## 3. 백엔드 구조 (walnutphoto 방식)

```
NR_ChildDay2026-BE/
  app/
    __init__.py        create_app, ProxyFix, 보안 헤더, CORS, 에러 핸들러
    config.py          환경변수 로드
    db.py              sqlite3 연결(WAL, foreign_keys), tx()=BEGIN IMMEDIATE, migrate()
    errors.py          ApiError, not_found, forbidden
    security.py        Argon2id 해시, new_token, 임시 비밀번호 생성
    auth.py            세션 쿠키, CSRF, login_required, superadmin_required, 비밀번호 변경 의무
    access.py          부스·행사 접근 판단(계정/부스 링크/실적 링크 공통)
    capacity.py        정원·타임 계산, 신청 상태 결정
    ratelimit.py       rate_events 기반 제한
    audit.py           감사 로그 기록
    cli.py             create-superadmin
    routes/
      public.py        방문객 API
      auth.py          로그인·로그아웃·me·비밀번호 변경
      admin_users.py   기관 계정 관리
      admin_events.py  행사·운영 단체
      admin_booths.py  부스·명단·실적
      links.py         부스 링크·실적 링크
      super.py         슈퍼관리자
      booth_ops.py     부스 작업 공통 구현(설정, 토글, 명단, 초기화, 통계, 내보내기)
  migrations/001_init.sql
  scripts/migrate_legacy.py
  tests/
```

- ORM을 쓰지 않고 `sqlite3`를 직접 사용한다. 쓰기 트랜잭션은 `BEGIN IMMEDIATE`로 직렬화한다.
- 기존 `app.py`, `fix_db.py`는 이관 완료 후 제거한다.

## 4. 데이터 모델

시간 값은 UNIX 초(REAL).

```sql
CREATE TABLE organizations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,          -- ^[a-z0-9][a-z0-9-]{1,31}$
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  org_id INTEGER REFERENCES organizations(id) ON DELETE RESTRICT,  -- 슈퍼관리자는 NULL
  username TEXT NOT NULL UNIQUE,      -- 소문자 정규화, ^[a-z0-9._-]{3,32}$
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 1,
  is_superadmin INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at REAL NOT NULL,
  last_login_at REAL,
  password_changed_at REAL,
  CHECK (is_superadmin = 1 OR org_id IS NOT NULL)
);

CREATE TABLE sessions (
  id_hash TEXT PRIMARY KEY,           -- sha256(쿠키 값)
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  created_at REAL NOT NULL, last_seen_at REAL NOT NULL, expires_at REAL NOT NULL,
  ip TEXT, user_agent TEXT
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX idx_events_org ON events(org_id);

CREATE TABLE partners (               -- 운영 단체
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  linked_org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
  access_token TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL
);
CREATE INDEX idx_partners_event ON partners(event_id);
CREATE INDEX idx_partners_linked ON partners(linked_org_id);

CREATE TABLE booths (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  partner_id INTEGER REFERENCES partners(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('time','fcfs')),
  use_waitlist INTEGER NOT NULL DEFAULT 0,
  total_limit INTEGER NOT NULL DEFAULT 0,      -- fcfs, 0 = 무제한
  start_hour INTEGER NOT NULL DEFAULT 11,      -- time
  end_hour INTEGER NOT NULL DEFAULT 16,
  slots_per_hour INTEGER NOT NULL DEFAULT 3,   -- 1~6 (A~F)
  limit_per_slot INTEGER NOT NULL DEFAULT 0,   -- 0 = 무제한
  is_active INTEGER NOT NULL DEFAULT 1,
  manage_token TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL
);
CREATE INDEX idx_booths_event ON booths(event_id);
CREATE INDEX idx_booths_partner ON booths(partner_id);

CREATE TABLE reservations (
  id INTEGER PRIMARY KEY,
  booth_id INTEGER NOT NULL REFERENCES booths(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  gender TEXT NOT NULL CHECK (gender IN ('남','여')),
  age_group TEXT NOT NULL,            -- 0~8세 | 9~13세 | 14~16세 | 17~19세 | 20~24세 | 24세 이상
  code TEXT NOT NULL,                 -- 식별번호 숫자 4자리
  slot TEXT NOT NULL,                 -- '11시 A타임' | '11시'(타임 1개) | '선착순'
  status TEXT NOT NULL CHECK (status IN ('normal','waiting','noshow','completed')),
  created_at REAL NOT NULL,
  UNIQUE (booth_id, name, code)
);
CREATE INDEX idx_res_booth_slot ON reservations(booth_id, slot);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  org_id INTEGER,                     -- 대상의 소유 기관 (기관 생성 등 전역 작업은 NULL)
  actor_org_id INTEGER,               -- 행위자 계정의 기관 (링크·슈퍼관리자는 NULL)
  user_id INTEGER,                    -- 계정 행위일 때
  actor TEXT NOT NULL,                -- 'user' | 'booth_link' | 'partner_link' | 'system'
  actor_label TEXT NOT NULL,          -- 기록 시점의 표시 이름 (예: '홍길동(hong)', '부스 링크: 페이스페인팅')
  action TEXT NOT NULL,               -- 예: 'booth.update'
  target TEXT,                        -- 예: 'booth:12'
  summary TEXT NOT NULL,              -- 사람이 읽는 한 줄 (예: '페이스페인팅 정원 100 → 120')
  detail TEXT,                        -- JSON (변경 전후 값 등)
  at REAL NOT NULL
);
CREATE INDEX idx_audit_org_at ON audit_log(org_id, at);
CREATE INDEX idx_audit_actor_org_at ON audit_log(actor_org_id, at);
CREATE TABLE rate_events (key TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX idx_rate_events ON rate_events(key, at);
```

### 규칙

- 계정은 기관 하나에만 속하며 역할 구분이 없다.
- "기본 행사(미분류)"는 두지 않는다. 행사 삭제 시 부스·신청이 함께 삭제된다(화면에서 이름 재입력 확인).
- 링크 토큰은 `secrets.token_urlsafe(18)`이며 원문으로 저장한다. QR을 언제든 다시 내려받아야 하기
  때문이다. 유출 시 재발급하면 이전 링크는 즉시 무효가 된다.
- 기관 비활성화 시 해당 기관 계정의 로그인과 방문객 주소(`/<slug>`), 해당 기관 행사의 링크가 모두 막힌다.
  데이터는 유지한다.

### 정원 규칙 (`capacity.py`)

- 정원에 포함되는 상태: `normal`, `completed`. `noshow`, `waiting`은 제외한다.
  노쇼 처리 시 자리가 다시 열린다.
- 타임별: 슬롯 목록은 `start_hour..end_hour`(양 끝 포함) × `slots_per_hour`.
  라벨은 타임이 1개면 `'{h}시'`, 아니면 `'{h}시 {A..F}타임'`(기존 데이터와 동일).
  슬롯별 점유 수 ≥ `limit_per_slot`(>0)이면 그 슬롯은 마감이다.
- 선착순: 점유 수 ≥ `total_limit`(>0)이면 마감이다.
- 마감 시 `use_waitlist`이면 `waiting`으로 접수하고, 아니면 409 `full`로 거절한다.
- 부스가 `is_active=0`이면 409 `closed`로 거절한다.
- 부스 전체 마감 = 대기 접수를 받지 않고, 모든 슬롯(또는 선착순 정원)이 마감인 상태.
- 대기자 → 확정은 관리자가 명단에서 수동 처리한다(`waiting` → `normal`). 이때 정원 초과 여부는 경고만 하고 막지 않는다.
- 신청 처리 전체(중복 확인, 점유 수 계산, 삽입)는 하나의 `BEGIN IMMEDIATE` 트랜잭션에서 수행한다.

### 집계 규칙

- 단순 합계: 선택 부스의 `noshow`를 제외한 전체 신청 건수.
- 엄격 합계: 위 집합에서 `(name, gender, age_group)`이 같으면 한 명으로 센다(현재 동작 유지).
- 표: 연령대 6구간 × 성별(남/여)과 합계.

## 5. 접근 주체와 권한

| 주체 | 인증 | 범위 |
|---|---|---|
| 방문객 | 없음 | 활성 기관의 운영 중 부스 조회, 신청, 본인 신청 조회·취소 |
| 부스 링크 | `/b/<manage_token>` | 해당 부스 1개의 관리 작업 |
| 실적 링크 | `/p/<access_token>` | 해당 운영 단체에 배정된 부스들의 관리 작업 + 합산 실적·내보내기 |
| 기관 계정 | 세션 | 소유 기관 행사·부스의 관리 작업과 소유 작업 + 연결 운영 단체 부스의 관리 작업 + 같은 기관 계정 관리 |
| 슈퍼관리자 | 세션 | 전체 |

- **관리 작업**: 부스 조회, 운영 시작/마감, 설정 수정(이름, 운영 방식, 정원, 시간, 타임 수, 대기자 접수),
  명단 조회, 상태 변경(노쇼·체험완료·대기→확정·정상 복귀), 신청 삭제, 명단 초기화, 통계, 내보내기.
- **소유 작업**(소유 기관 계정과 슈퍼관리자만): 행사 생성·수정·삭제, 부스 생성·삭제·일괄 등록,
  다른 행사로 이동, 운영 단체 생성·수정·삭제·배정, 링크 재발급, 일괄 운영 상태 변경.

### 접근 판단 (`access.py`)

```python
class Principal:  # 요청마다 하나
    kind: Literal["user", "booth_link", "partner_link"]
    user: User | None
    booth_id: int | None      # booth_link
    partner_id: int | None    # partner_link

def booth_access(p: Principal, booth_id: int) -> Literal["own", "manage"]:
    """부스가 없거나 접근 불가면 404. 소유 기관/슈퍼관리자는 'own', 그 외 허용은 'manage'."""

def require_booth(p, booth_id, need: Literal["manage", "own"] = "manage") -> Row
def require_event(user, event_id) -> Row      # 소유 기관만 (연결 기관은 행사 단위 접근 없음)
def require_org(user, org_id) -> Row          # 자기 기관 또는 슈퍼관리자
```

- 판단 순서: 대상 없음/범위 밖 → 404, 범위 안이지만 `own` 필요 → 403.
- 연결 기관 판정: `booths.partner_id → partners.linked_org_id = user.org_id`.
- `booth_ops.py`의 작업 함수는 `(principal, booth_row, payload)`를 받고, 라우트는 접근 판단만 다르게 한다.

## 6. 인증

walnutphoto(`server/app/auth.py`, `security.py`, `ratelimit.py`)의 구현을 옮기되 다음을 바꾼다.

- 로그인: `{username, password}`. 아이디+IP 기준 15분 10회 실패 시 429.
  존재하지 않는 아이디도 더미 해시로 검증해 응답 시간을 맞춘다.
  비활성 계정 또는 비활성 기관 계정은 `invalid_credentials`와 같은 응답.
- 세션: 쿠키 `nr_session`(HttpOnly, Secure, SameSite=Lax, Path=/), DB에는 sha256만 저장.
  유휴 12시간, 절대 7일. 로그인 시 기존 세션 폐기(세션 고정 방지).
- CSRF: 세션별 토큰, `/api/admin/` 하위 변경 요청에 `X-CSRF-Token` 필수. 예외: `auth/login`.
- 비밀번호 변경 의무: `must_change_password=1`이면 `auth/me`, `auth/password`, `auth/logout` 외
  모든 `/api/admin/` 요청이 403 `password_change_required`.
- 비밀번호: 8자 이상, 아이디와 같으면 거부. 변경 시 현재 세션 외 모든 세션 폐기.
- 임시 비밀번호: 혼동 문자(0/O, 1/l/I)를 뺀 영숫자 10자. 응답으로 한 번만 반환하고 저장은 해시만.
- 계정 발급·초기화·비활성화: 같은 기관 계정 또는 슈퍼관리자. 초기화 시 대상의 모든 세션 폐기 +
  `must_change_password=1`. 자기 자신 비활성화와 기관의 마지막 활성 계정 비활성화는 409.
- 슈퍼관리자: `flask create-superadmin --username --password`(있으면 승격 + 비밀번호 덮어쓰기).
  슈퍼관리자 계정은 기관 계정이 초기화·비활성화할 수 없다.
- 교차 출처: 프론트 `https://nrbooth.team-cluster.kr`, API `https://child-api.team-cluster.kr`.
  같은 사이트이므로 Lax 쿠키가 `credentials: 'include'` 요청에 전송된다.
  CORS는 `CORS_ORIGINS` 목록만 허용, `supports_credentials=True`, 허용 헤더 `Content-Type, X-CSRF-Token`.

### 감사 로그

기록 대상(같은 트랜잭션 안에서 기록):

| 분류 | action |
|---|---|
| 계정 | `auth.login`, `auth.password_change`, `user.create`, `user.update`, `user.reset_password`, `user.deactivate`, `user.activate` |
| 기관 | `org.create`, `org.update`(슈퍼관리자), `org.logo`(detail `{action: set|delete}`) |
| 행사·운영 단체 | `event.create`, `event.update`(detail에 바뀐 항목 `{from,to}`, notice 포함), `event.logo`(detail `{action: set|delete}`), `event.delete`, `partner.create`, `partner.update`, `partner.delete`, `partner.rotate_token` |
| 부스 | `booth.create`, `booth.bulk_create`, `booth.update`, `booth.move`, `booth.delete`, `booth.toggle`, `booth.batch_status`, `booth.rotate_token` |
| 명단 | `reservation.delete`, `reservation.clear`(초기화), `export` |
| 기타 | `backup.download` |

- 신청 접수, 방문객 취소, 노쇼·체험완료 같은 상태 변경은 건수가 많고 현장 작업이라 기록하지 않는다.
  (명단 자체에 상태가 남는다.)
- 로그인 실패는 기록하지 않는다(제한 테이블로 처리).
- 기관 계정이 보는 범위: `org_id = 내 기관` 또는 `actor_org_id = 내 기관`.
  즉 내 기관 행사에서 일어난 일(링크·연결 기관 작업 포함)과 내 계정들이 다른 기관 부스에서 한 일.
- 슈퍼관리자는 전체를 본다.
- 보존 기간 제한은 두지 않는다(건수가 적다).

### 제한

| 키 | 한도 |
|---|---|
| `login:{username}:{ip}` | 15분 10회 실패 |
| `link-miss:{ip}` | 잘못된 링크 토큰 15분 30회 |
| `lookup:{ip}` | 방문객 조회·취소 15분 20회 |
| `reserve:{ip}` | 신청 1분 30회 |

`rate_events`는 24시간 지난 행을 주기적으로 정리한다.

## 7. API

오류 형식: `{ "error": "<code>", "fields": { ... } }` (`fields`는 `invalid`일 때만). 코드와 상태:

| 상태 | 코드 |
|---|---|
| 401 | `unauthorized`(세션 없음·만료 — 프론트는 로그인 화면으로), `invalid_credentials`(로그인 실패 전용) |
| 403 | `csrf`, `forbidden`, `password_change_required` |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 409 | `full`(정원 마감), `closed`(부스 마감), `duplicate`(같은 이름+식별번호 신청 중복), `not_cancellable`(`noshow`·`completed` 취소), `cannot_deactivate_self`, `use_password_change`(자기 비밀번호 초기화 시도), `last_active_user`(기관의 마지막 활성 계정 비활성화), `username_taken`, `slug_taken` |
| 413 | `file_too_large`(로고 파일 1MB 초과·과대 요청 본문) |
| 422 | `invalid` + `fields:{<필드>:<메시지>}` |
| 429 | `rate_limited` |

`POST /api/admin/auth/password`에서 현재 비밀번호가 틀리면 401이 아니라 **`422 invalid`, `fields:{current:"현재 비밀번호가 일치하지 않습니다."}`**
(프론트가 401을 "세션 끊김"으로 처리하기 때문). 이 요청도 로그인과 같은 시도 제한(`login:{username}:{ip}`)을 쓴다.

### 7.1 방문객 `/api/public`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/orgs/<slug>` | `{org:{name,slug,logo_url}, events:[{id,name,logo_url,notice,booths:[{id,name,mode,is_closed}]}]}` — 운영 중 부스가 있는 행사만 |
| GET | `/orgs/<slug>/booths/<id>` | `{org:{name,slug,logo_url}, event:{id,name,logo_url,notice}, booth:{id,name,mode,use_waitlist,is_active}, availability}`. 기관 불일치·비활성 기관 404. `availability`는 모드별 두 가지(아래) |
| POST | `/booths/<id>/reservations` | `{name, gender, age_group, code, slot?}` → `{id, slot, status}` |
| POST | `/booths/<id>/lookup` | `{name, code}` → `{name, slot, status, booth_name}` 또는 404 |
| POST | `/booths/<id>/cancel` | `{name, code}` → 204. `noshow`·`completed`는 409 |
| GET | `/booths/<id>/home` | `{slug}` — 기존 `/reserve/<id>` 리다이렉트용 |
| GET | `/assets/<name>` | 로고 이미지. 이름은 `<sha256 hex 64자>.png·jpg·webp` 형식만 허용(그 외 404). `Cache-Control: public, max-age=31536000, immutable`, 이미지 Content-Type, `X-Content-Type-Options: nosniff` |

`availability` (`capacity.py`): 정원 0은 무제한이라 `remaining`이 `null`, `full`은 항상 false.
`is_closed` = 운영 중지이거나 (대기 미사용이면서 마감).
- 선착순(`mode: "fcfs"`): `{remaining: int|null, full: bool, is_closed: bool}`
- 시간대별(`mode: "time"`): `{slots:[{label, remaining: int|null, full: bool}], is_closed: bool}` — 슬롯 라벨은 `11시 A타임`(시간당 1칸이면 `11시`)

신청 오류: 이름·식별번호·성별·연령대 검증 실패와 목록에 없는 슬롯은 `422 invalid`, 중복 `409 duplicate`, 정원 `409 full`(대기 사용 시 `waiting`으로 접수), 마감 `409 closed`.
`lookup`·`cancel`은 같은 IP 15분 20회 제한을 공유한다. 응답에 다른 신청자의 정보는 포함하지 않는다.

### 7.2 인증 `/api/admin/auth`

`POST /login` `{username, password}` → `{user, csrf}` (실패 `401 invalid_credentials`, 시도 초과 `429`) ·
`POST /logout` → 204 · `GET /me` → `{user, csrf, org}` (`org`는 `{id,name,slug,logo_url}`, 슈퍼관리자는 `null`) ·
`POST /password` `{current, new}` → 204 (현재 비밀번호 오류는 위 `422 fields.current`, 새 비밀번호 규칙 위반은 `422 fields.new`).
`user` = `{id, username, display_name, org_id, is_superadmin, must_change_password}`.
`must_change_password`면 `login`·`me`·`password`·`logout` 외 `/api/admin/*`는 `403 password_change_required`.
CSRF: 로그인을 뺀 모든 변경 요청에 `X-CSRF-Token`(로그인·`me`가 돌려준 값) 필요. 없거나 틀리면 `403 csrf`.

### 7.3 기관 관리 `/api/admin`

| 메서드 | 경로 | 권한 |
|---|---|---|
| GET/POST | `/orgs/<org>/users` | require_org. GET `{users:[user]}`. POST는 `{username, display_name}` → 201 `{user, temp_password}` (`username_taken` 409). 여기서 `user` = `{id, username, display_name, is_active, must_change_password, last_login_at, created_at}` |
| PATCH | `/users/<id>` | 같은 기관. `{display_name?, is_active?}` → `{user}`. 자기 자신 비활성화 `cannot_deactivate_self`, 마지막 활성 계정 `last_active_user`(409) |
| POST | `/users/<id>/reset-password` | 같은 기관 → `{temp_password}`. 자기 자신은 `409 use_password_change` |
| GET | `/orgs/<org>/overview` | require_org. 소유 행사 + 연결 운영 단체(행사·주최 기관 이름 포함)와 부스 요약, 오늘·전체 신청 수, 운영 중 부스 수 |
| GET/POST | `/orgs/<org>/events` | require_org. GET `{events:[event]}`, POST 201 `{event}`. `event` = `{id, name, created_at, booth_count, partner_count, logo_url, notice}` |
| PATCH/DELETE | `/events/<id>` | require_event. PATCH `{name?, notice?}`(둘 다 없으면 422; `notice`는 앞뒤 공백 제거 후 0~500자 문자열 또는 null, 빈 값은 null로 저장) → `{event}`, DELETE 204 |
| PUT | `/orgs/<org>/logo` | require_org. multipart `file`(PNG·JPEG·WebP, 시그니처로 판별, 1MB 이하; SVG 거부) → `{logo_url}`. 파일 없음·빈 파일·형식 오류 422, 초과 413 |
| DELETE | `/orgs/<org>/logo` | require_org → 204 |
| PUT | `/events/<id>/logo` | require_event. 위와 같음 → `{logo_url}` |
| DELETE | `/events/<id>/logo` | require_event → 204 |
| GET/POST | `/events/<id>/partners` | require_event. POST `{name, linked_org_id?}` → 201 `{partner}`. GET `{partners:[partner]}`. `partner` = `{id, name, linked_org:{id,name}|null, access_token, booth_count}` |
| PATCH/DELETE | `/partners/<id>` | 소유. PATCH `{name?, linked_org_id?}` → `{partner}`. 삭제 시 배정 부스는 미배정으로, 204 |
| POST | `/partners/<id>/rotate-token` | 소유 → `{access_token}` |
| GET | `/orgs/<org>/link-targets` | 연결 기관 선택용 활성 기관 목록(자기 기관 제외) `{orgs:[{id, name}]}` |
| GET/POST | `/events/<id>/booths` | require_event. GET `{booths:[booth]}`, POST 201 `{booth}` (`booth`는 아래 부스 객체) |
| POST | `/events/<id>/booths/bulk` | require_event. `{rows:[...]}`(최대 500): 전부 검증 후 일괄 삽입 → 201 `{created:n}`, 하나라도 오류면 `422 {fields:{rows:{<index>:<message>}}}` |
| GET/PATCH/DELETE | `/booths/<id>` | GET `{booth}`, PATCH `{booth}`(관리 항목은 manage, `event_id`·`partner_id` 변경과 DELETE는 own; 행사를 옮기면 운영 단체는 미배정), DELETE 204 |
| POST | `/booths/<id>/toggle` | manage → `{is_active}` (DB에서 원자적으로 뒤집은 값) |
| POST | `/booths/batch-status` | `{booth_ids, is_active}` — 모든 부스에 own 필요, 하나라도 아니면 404로 아무것도 바꾸지 않음 → `{updated:n}`. 감사 기록은 부스 소유 기관마다 1건 |
| POST | `/booths/<id>/rotate-token` | own → `{manage_token}` |
| GET | `/booths/<id>/reservations` | manage → `{reservations:[reservation]}` (슬롯 시각순) |
| PATCH/DELETE | `/reservations/<id>` | 소속 부스 manage. PATCH `{status}`(`normal|waiting|noshow|completed`) → `{reservation, over_capacity?}`, DELETE 204 |
| DELETE | `/booths/<id>/reservations` | manage (초기화) → `{deleted:n}` |
| POST | `/stats` | `{booth_ids(1~500), mode: simple|strict}` — 모든 부스에 manage 필요 → `{rows:[{age_group,male,female,total}], male, female, total}` (`noshow` 제외, strict는 (이름,성별,연령대) 중복 제거) |
| POST | `/export` | `{booth_ids(1~500)}` → `{booths:[{booth_id, event_name, booth_name, rows:[{name,gender,age_group,code,slot,status,created_at}]}]}`. 감사 기록은 **부스 소유 기관마다 1건**(그 기관 부스 id만 `detail.booth_ids`에 담김) |
| GET | `/orgs/<org>/audit` | require_org. `?before=<id>&limit=50(최대 200)&action=<접두사>` → `{items:[{id, org_id, actor_org_id, user_id, actor, actor_label, action, target, summary, detail, at}], next_before}` 최신순. 대상 기관이거나 행위자 기관인 행이 보인다 |

객체 형태:
- `booth` = `{id, event_id, event_name, org_id, partner_id, partner_name, name, mode, use_waitlist, total_limit, start_hour, end_hour, slots_per_hour, limit_per_slot, is_active, manage_token, count, availability}` (`count`는 `noshow` 제외 신청 수, `availability`는 7.1과 같은 모양)
- `reservation` = `{id, booth_id, name, gender, age_group, code, slot, status, created_at}`
- `GET /orgs/<org>/overview` → `{org:{id,name,slug}, owned:[{event:{id,name}, booths:[booth]}], linked:[{partner:{id,name}, event:{id,name}, host:{id,name}, booths:[booth]}], totals:{today, total, active_booths, booths}}`

### 7.4 링크 `/api/link`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/booth/<t>` | `{booth, org:{name,slug}, reservations:[reservation], stats}` (`stats`는 simple 모드 결과) |
| PATCH | `/booth/<t>` | 관리 항목(이름·방식·대기·정원·시간 설정)만 → `{booth}`. 다른 키가 섞이면 403 |
| POST | `/booth/<t>/toggle` | 운영 시작/마감 → `{is_active}` |
| PATCH/DELETE | `/booth/<t>/reservations/<rid>` | 상태 변경 `{reservation, over_capacity?}`·삭제 204 |
| DELETE | `/booth/<t>/reservations` | 초기화 → `{deleted:n}` |
| GET | `/booth/<t>/export` | 내보내기 (7.3 `/export`와 같은 응답) |
| GET | `/partner/<t>` | `{partner:{id,name}, event:{id,name}, org:{name,slug}, booths:[booth]}` |
| POST | `/partner/<t>/stats` · `/partner/<t>/export` | `booth_ids`(생략하면 배정 부스 전체)는 배정 부스 범위 안이어야 함. export는 대상이 없으면 `{booths:[]}` |
| GET/PATCH | `/partner/<t>/booths/<id>` | 배정 부스만. `{booth}` |
| POST | `/partner/<t>/booths/<id>/toggle` | → `{is_active}` |
| GET/DELETE | `/partner/<t>/booths/<id>/reservations` | 목록 `{reservations}` · 초기화 `{deleted}` |
| PATCH/DELETE | `/partner/<t>/booths/<id>/reservations/<rid>` | 부스 링크와 동일 |

링크 토큰이 틀리면 `404 not_found`(같은 IP 15분 30회 제한 후 429).

### 7.5 슈퍼관리자 `/api/admin/super`

`GET /orgs` → `{orgs:[{id,name,slug,is_active,created_at,user_count,event_count}]}` · `POST /orgs` `{name, slug}` → 201 `{org}`
(`slug_taken` 409, 예약어·형식 위반 422) · `PATCH /orgs/<id>` `{name?, slug?, is_active?}` → `{org}`
(비활성화하면 소속 계정 세션 모두 삭제) · `GET /backup`(일관된 DB 사본 다운로드) ·
`GET /audit`(전체 감사 로그, 7.3과 같은 쿼리 파라미터 + `org_id` 필터) ·
`GET /whoami-ip` → `{remote_addr, x_forwarded_for}` (`TRUSTED_PROXY_HOPS` 점검용, README 운영 전환 절차 참고).
`org.create` 감사 행의 `org_id`는 새 기관의 id, `backup.download`는 `org_id` NULL.
기관 계정 관리는 7.3을 사용한다(슈퍼관리자는 require_org 통과).

### 7.6 보안 헤더

`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
`/api/*`에 `Cache-Control: no-store`, `X-Robots-Tag: noindex`.
링크 토큰이 URL에 있으므로 프론트 쪽도 `Referrer-Policy: no-referrer`를 설정한다(2단계).

## 8. 데이터 이관

`python scripts/migrate_legacy.py --legacy <기존 reservations.db> --out <DATA_DIR>/app.db`

1. `--out` 파일이 있으면 중단한다. 새 DB에 마이그레이션을 적용한다.
2. 기관 생성: `나름청소년활동센터`(slug `nareum`), `광명시청소년재단`(slug `gmyouth`).
3. 행사 매핑: 이름이 `2026년 광명시 청소년의 달 기념행사 오월의 난장`인 행사 → `gmyouth`.
   `기본 행사(미분류)`와 그 부스·신청은 버린다. 나머지 행사 → `nareum`.
   위 두 이름 중 하나라도 기존 DB에서 찾지 못하면 중단한다(운영 DB와 로컬 사본의 차이 대비).
4. 부스·신청은 기존 ID를 유지한다. 부스마다 `manage_token`을 발급하고, 운영 단체는 미배정으로 둔다.
5. 정리: `time` → `slot`(`'선착순 접수'` → `'선착순'`), `phone` → `code`, `ageGroup` → `age_group`,
   `created_at` 문자열 → UNIX 초(NULL이면 부스 생성 시각), `status` NULL → `normal`,
   `use_waitlist` NULL → 0. `event_id` NULL 부스는 기본 행사로 간주해 버린다.
6. 행사·부스·상태별 건수를 기존 DB와 대조한 보고서를 출력한다(버린 건수 별도 표기).
7. 계정은 만들지 않는다. CLI로 슈퍼관리자를 만든 뒤 화면에서 발급한다.

## 9. 배포

- 1·2단계는 함께 배포한다. 새 API는 기존 프론트와 호환되지 않는다.
- 절차: 운영 DB 백업 → 이관 스크립트 → 새 컨테이너 기동 → `flask create-superadmin` →
  기관 계정 발급 → 기존 관리자 QR을 부스 링크 QR로 교체 안내.
- Docker: `python:3.11-slim`, 의존성 `flask flask-cors argon2-cffi python-dotenv gunicorn`.
  `requirements.txt`로 버전 고정. DB는 볼륨 `DATA_DIR`에 둔다.
- 환경변수: `DATA_DIR`, `CORS_ORIGINS`, `SESSION_COOKIE_SECURE`(기본 1), `TRUSTED_PROXY_HOPS`.

## 10. 테스트 (pytest)

- 격리: 모든 `/api/admin/` 라우트를 다른 기관 계정으로 호출하면 404.
  `app.url_map`을 순회해 테스트 케이스가 모든 라우트를 덮는지 검사한다.
  슈퍼 라우트는 기관 계정에 403.
- 링크: 부스 링크로 다른 부스 접근 404, 실적 링크로 미배정 부스 404, 링크로 소유 작업 불가,
  재발급 후 이전 토큰 404, 비활성 기관의 링크 404.
- 연결 기관: 관리 작업 허용, 소유 작업 403, 연결 해제 후 404.
- 정원: 스레드 동시 신청에서 초과 없음, 노쇼 처리 후 자리 회복, 대기자 접수, 마감 거절, 중복 거절.
- 방문객: 조회·취소는 본인만, 다른 사람 정보 미노출, 조회 제한 429.
- 감사 로그: 기록 대상 작업이 로그를 남기는지, 기관 계정이 다른 기관 로그를 볼 수 없는지,
  연결 기관 계정의 작업이 소유 기관과 행위자 기관 양쪽에 보이는지.
- 인증: 로그인 실패 제한, 비활성 계정 거부, 비밀번호 변경 의무, 초기화 시 세션 폐기,
  마지막 활성 계정 보호, CSRF 누락 403.
- 이관: 기존 스키마로 만든 작은 DB를 이관해 매핑·정리·건수를 검증한다.

## 11. 2단계(프론트엔드)가 다룰 범위

- 방문객: `/:slug`(부스 목록), `/:slug/reserve/:id`, `/:slug/check/:id`.
  기존 `/reserve/:id`, `/check/:id`는 `/booths/<id>/home`으로 slug를 얻어 리다이렉트.
  확인 화면은 lookup/cancel API 사용.
- 관리: `/manage/login`, 비밀번호 변경 강제 화면, 대시보드(소유 행사 + 참가한 연합 행사),
  행사·운영 단체·부스·명단·합계실적·운영마감, 계정 관리, 활동 기록(감사 로그) 열람, 슈퍼관리자.
- 링크: `/b/:token`(부스 관리), `/p/:token`(운영 단체 실적과 부스 관리).
- QR·링크 엑셀은 새 링크 기준으로 생성.
- API 클라이언트: `credentials: 'include'`, CSRF 헤더, 401 시 로그인 이동.

## 12. 범위 밖

이메일 발송, 공개 가입, 계정의 다중 기관 소속, 역할 구분, 대기자 자동 승급과 알림,
기관별 저장 용량 제한, 감사 로그 보존 기간 관리.
