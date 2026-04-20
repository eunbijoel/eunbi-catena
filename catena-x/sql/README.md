# SQL 스키마 (팀 ER 다이어그램 정렬)


| 파일                             | 용도                                    |
| ------------------------------ | ------------------------------------- |
| `postgres_cobot_telemetry.sql` | PostgreSQL용 테이블 정의 (팀원 DB에 그대로 적용 가능) |


## 테이블 역할 (한 줄)

- **cobot_telemetry_raw** — 수신 원본 보존 (`payload` JSON + 메타)
- **cobot_telemetry_latest** — 로봇별 최신 한 줄 (빠른 조회)
- **cobot_measurements** — 분석·검색용 정규화 컬럼 (`RawTelemetry`와 동일 필드군)
- **cobot_aas_sync_status** — AAS 반영 상태 (`Pending` / `Synced` / `Failed` 등)
- **cobot_access_audit** — 접근·감사 로그

## 앱 코드와의 연결

- 컬럼 이름·흐름은 `apps/telemetry_db.py` 와 맞춰 두었습니다.
- **로컬 미리 구현**: 환경 변수 `COBOT_TELEMETRY_DB=/path/to/file.db` 를 주면 `server/catena_app.py` / `server/app.py` 가 텔레메트리 수신 시 **SQLite**에 위와 같은 테이블을 만들고 같은 순서로 씁니다. 팀 **PostgreSQL**로 갈 때는 이 DDL을 DB에 적용한 뒤, **같은 테이블·같은 순서로 INSERT** 하도록 코드만 Postgres용으로 바꾸면 됩니다. (URL만 넣는다고 끝나는 건 아니고, 접속 라이브러리 등이 필요합니다.)
- **참고**: 일부 Python 빌드에는 `sqlite3` 가 없을 수 있습니다. 그때는 `COBOT_TELEMETRY_DB` 를 비우면 기존처럼 JSON 파일만 저장되고, DB 미러만 건너뜁니다.