# NR 부스 예약 백엔드

여러 기관(멀티테넌트)이 행사·부스 예약을 운영하는 Flask + SQLite API 서버.
설계와 API 명세는 [docs/superpowers/specs/2026-10-29-multitenant-design.md](docs/superpowers/specs/2026-10-29-multitenant-design.md) 참고.

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env              # 필요하면 수정. 로컬 HTTP 개발이면 SESSION_COOKIE_SECURE=0
flask --app wsgi.py run -p 5005
```

DB는 `DATA_DIR`(기본 `./data`)의 `app.db`에 자동 생성·마이그레이션된다.
헬스체크: `GET /api/health`.

## 테스트

```bash
python -m pytest -q
```

## 환경변수

`.env` 파일 또는 프로세스 환경변수로 지정한다. 모두 생략 가능.

| 이름 | 기본값 | 의미 |
|---|---|---|
| `DATA_DIR` | `./data` (Docker: `/data`) | `app.db`를 두는 폴더 |
| `CORS_ORIGINS` | `http://localhost:5173,https://nrbooth.team-cluster.kr,https://2026child.team-cluster.kr` | CORS 허용 출처(쉼표 구분) |
| `SESSION_COOKIE_SECURE` | `1` | 세션 쿠키 Secure 속성. `0`이면 HTTP에서도 전송(로컬 개발용) |
| `TRUSTED_PROXY_HOPS` | `2` | 신뢰할 프록시 단수(Cloudflare 1 + 리버스 프록시 1). 직접 연결이면 `0` |
| `CLEANUP_INTERVAL` | `600` | 오래된 보조 행 정리 주기(초) |

## 슈퍼관리자 생성·복구

```bash
flask --app wsgi.py create-superadmin --username root          # 비밀번호는 숨김 입력으로 두 번 묻는다
# Docker (이미지에 FLASK_APP=wsgi.py 가 설정되어 있다)
docker exec -it <컨테이너> flask create-superadmin --username root
```

이미 있는 아이디면 슈퍼관리자로 승격하고 비밀번호를 바꾼다. 비밀번호를 잊었거나 슈퍼관리자 계정을 잃었을 때의 복구도 같은 명령이다.

## Docker

```bash
docker build -t nr-booth-be .
docker run -d --name nr-booth-be -p 5005:5005 -v /volume1/docker/nr-booth/data:/data nr-booth-be
```

포트 5005, 워커 4개(`gunicorn.conf.py`). DB는 `/data` 볼륨에 저장되므로 컨테이너를 지우고 다시 만들어도 남는다.

## 운영 전환 절차

기존(단일 행사) 서비스를 새 멀티테넌트 서버로 바꾸는 순서. 1·2단계(백엔드·프론트)는 함께 배포한다. 새 API는 기존 프론트와 호환되지 않는다.

1. **백업**: NAS의 운영 DB(`reservations.db`)를 복사해 둔다. 이관 스크립트는 원본을 읽기 전용으로만 열지만 백업은 필수다.
2. **새 이미지 빌드**: `docker build -t nr-booth-be .`
3. **이관**: 새 이미지로 스크립트를 실행해 `/data/app.db`를 만든다. 출력 파일이 이미 있으면 거부한다.
   ```bash
   docker run --rm \
     -v /volume1/docker/nr-booth/old:/old:ro \
     -v /volume1/docker/nr-booth/data:/data \
     nr-booth-be python scripts/migrate_legacy.py --legacy /old/reservations.db --out /data/app.db
   ```
   (로컬: `python scripts/migrate_legacy.py --legacy PATH --out PATH`)
   끝에 출력되는 **이관 보고서의 건수**(기관별 행사/부스/신청, 버려진 항목, 상태별 건수)를 기존 DB와 대조해 확인한다.
   - 중복 신청, 허용 범위 밖 값 등이 있으면 `이관 실패: ...`와 해당 행 id를 출력하고 아무것도 남기지 않는다. 기존 DB를 정리한 뒤 다시 실행한다.
   - 보고서의 "조정된 부스 값"에 새 슬롯 목록에 없는 신청 건수가 표시되면 해당 부스를 행사 전에 점검한다.
4. **새 컨테이너 기동**: 위 `docker run -d ...` 후 시작 로그에서 `DB 위치: /data/app.db / 스키마 버전: N` 확인.
5. **슈퍼관리자 생성**: `docker exec -it nr-booth-be flask create-superadmin --username root`
6. **기관 계정 발급**: 슈퍼관리자로 로그인해 기관별 계정을 만든다(이관된 기관: `nareum`, `gmyouth`). 첫 로그인 때 비밀번호 변경이 강제된다.
7. **안내**: 기존 관리자 QR은 더 이상 쓰이지 않는다. 각 부스의 새 링크(`/b/<token>`)로 만든 QR로 교체해 배부한다.

## API 개요

인증(세션 + CSRF 헤더), 방문객·기관 관리자·슈퍼관리자·부스 링크 API의 경로와 권한은
[설계 문서 7절](docs/superpowers/specs/2026-10-29-multitenant-design.md)을 참고한다. 모든 경로는 `/api/` 아래에 있다.
