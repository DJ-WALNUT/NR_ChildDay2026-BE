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
