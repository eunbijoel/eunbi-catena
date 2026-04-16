# Catena-X 협동로봇 텔레메트리 PoC

공장 협동로봇 JSON 텔레메트리를 **전처리 → AAS Shell/Submodel → EDC 자산·정책·계약(mock)** 흐름으로 다루는 샘플 코드입니다.  
실제 커넥터 없이 **로컬 JSON 저장소**로 동작합니다.

---

## 디렉터리 구조


| 경로                                   | 용도                                                                             |
| ------------------------------------ | ------------------------------------------------------------------------------ |
| `**apps/catenax/`**                  | 파이프라인 핵심 코드                                                                    |
| `apps/catenax/edc.py`                | CLI 진입점 (`onboard`, `sync-aas`, `list`, `export-catalog`) 및 `CobotEDCPipeline` |
| `apps/catenax/models.py`             | `RawTelemetry`, EDC/AAS 데이터 클래스                                                |
| `apps/catenax/aas_mapper.py`         | 전처리(`TelemetryPreprocessor`) + AAS 매핑(`AASMapper`)                             |
| `apps/catenax/ai_helpers.py`         | Ollama 보조(선택, `--use-ai`)                                                      |
| `apps/catenax/edc_monolith.py`       | 이전 단일 파일 버전(참고용)                                                               |
| `apps/catenax/sample_telemetry.json` | **기본 샘플** — 단일 로봇 JSON (EDC HttpData·문서 예제와 동일 형식) |
| `apps/catenax/sample_telemetry_fleet.json` | **플릿 데모** — 객체 배열 (여러 대, `--all-records`용) |
| **`server/`** | HTTP 서버 |
| `server/app.py` | **텔레메트리 수신 백엔드** — `POST/GET` 저장 `server/data/`, EDC 온보딩 시 `cobot-api-base-url` 로 연결 |
| `server/catena_app.py` | **대시보드 UI** — `dashboard.html`, `/api/dashboard` (mock `store/` 읽기) |
| `server/dashboard.py` | 경량 KPI 대시보드, `Run Pipeline` |
| `**dashboard.html`**                 | `catena_app.py`가 서빙하는 프론트(차트·카드 UI)                                            |
| `**store/`**                         | (생성됨, git 제외) mock 결과 — `store/aas/`, `store/edc/`                             |
| `**data/`**                          | (생성됨, git 제외) `dashboard.py`용 mock 경로 등 로컬 실험 데이터                              |


---

## 서버 흐름 (센터장님 워크플로와 동일)

1. **텔레메트리 서버** (`server/app.py`) — 공장/PLC/앱이 `POST` 로 JSON 전송 → `server/data/YYYY-MM-DD/*.json` + `server/data/latest.json`.
2. **EDC 온보딩** (`edc.py onboard`) — HttpData 자산이 위 API (`/api/v1/cobot/telemetry`)를 가리키도록 mock(또는 실제 Management URL) 등록.
3. **AAS 동기화** (`edc.py sync-aas`) — 파일/저장 텔레메트리 → AAS 서브모델 upsert (mock 시 `store/aas/`).

```bash
cd catena-x
python3 server/app.py --host 0.0.0.0 --port 8080
```

| 엔드포인트 | 설명 |
|------------|------|
| `GET /health` | 헬스 |
| `POST /api/v1/cobot/telemetry` | 수신 (필수 필드 검증, 실패 시 400) |
| `GET /api/v1/cobot/telemetry/latest` | `latest.json` 또는 `?robot_id=` |
| `GET /api/v1/cobot/telemetry?limit=20` | 최근 N건 |

예시:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/cobot/telemetry \
  -H "Content-Type: application/json" \
  -d @apps/catenax/sample_telemetry.json
```

---

## 빠른 시작 (mock EDC/AAS)

작업 디렉터리: **`catena-x`**

```bash
cd catena-x
export CATENAX_STORE_DIR="$PWD/store"
mkdir -p store

# 단일 로봇 샘플로 온보딩 (기본)
python3 apps/catenax/edc.py onboard \
  --telemetry-json apps/catenax/sample_telemetry.json \
  --provider-bpn BPNL000000000001

# 플릿(배열) 전체 온보딩
python3 apps/catenax/edc.py onboard \
  --telemetry-json apps/catenax/sample_telemetry_fleet.json \
  --provider-bpn BPNL000000000001 \
  --all-records

# BerePi 스타일: EDC 에셋 ID 고정
python3 apps/catenax/edc.py onboard \
  --telemetry-json apps/catenax/sample_telemetry.json \
  --provider-bpn BPNL000000000001 \
  --asset-id cobot-01-telemetry \
  --cobot-api-base-url http://127.0.0.1:8080

python3 apps/catenax/edc.py list
python3 apps/catenax/edc.py export-catalog
```

### 대시보드 (GitHub 스타일 UI)

```bash
cd catena-x
export CATENAX_STORE_DIR="$PWD/store"
python3 server/catena_app.py --port 8765
```

브라우저: **http://127.0.0.1:8765/dashboard.html** (포트 충돌 시 `--port` 변경).  
`server/app.py`(8080)와 동시에 띄울 수 있습니다(서로 다른 포트).

### 경량 대시보드 (`dashboard.py`)

```bash
cd catena-x
export CATENAX_MOCK_DATA_DIR="$PWD/data/catena_mock"
python3 server/dashboard.py
```

기본 **8765** — `GET /api/summary`, `POST /api/pipeline/run` 등.

---

## 환경 변수 (요약)


| 변수                           | 설명                                               |
| ---------------------------- | ------------------------------------------------ |
| `CATENAX_STORE_DIR`          | mock 저장 루트 (`store/aas`, `store/edc`). **권장.**   |
| `CATENAX_MOCK_DATA_DIR`      | 예전 이름; `CATENAX_STORE_DIR`와 동일 역할로 `edc.py`에서 호환 |
| `CATENAX_EDC_MANAGEMENT_URL` | 설정 시 실제 EDC Management API 사용                    |
| `CATENAX_AAS_BASE_URL`       | 설정 시 실제 BaSyx AAS 사용                             |
| `OLLAMA_*` | `--use-ai` 시 Ollama |


---

## JSON 수정 후 반영

1. `apps/catenax/sample_telemetry.json` 편집·저장
2. 다시 온보딩:
  `python3 apps/catenax/edc.py onboard ... --all-records`
3. 대시보드 새로고침 (같은 `CATENAX_STORE_DIR` 사용)

---

## CLI 요약


| 명령                                            | 설명                             |
| --------------------------------------------- | ------------------------------ |
| `onboard --telemetry-json … --provider-bpn …` | 전체 파이프라인                       |
| `onboard … --all-records`                     | 배열의 **모든** 레코드 처리 (기본은 첫 레코드만) |
| `sync-aas --telemetry-json …`                 | AAS만 갱신                        |
| `list`                                        | 등록된 에셋·AAS 요약                  |
| `export-catalog`                              | 로컬 카탈로그 JSON                   |


---

## 표준·참고

- Eclipse EDC (Tractus-X) Management API  
- AAS / IDTA 협동로봇 서브모델 개념  
- Catena-X 데이터 스페이스 PoC 수준 (실제 계약 협상·전송은 비목표)

---

## 상위 저장소

이 폴더는 저장소 루트 `eunbi` 아래에 있으며, Catena-X와 무관한 파일은 `../labs/` 등에 둡니다.