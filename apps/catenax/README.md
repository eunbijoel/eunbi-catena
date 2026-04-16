# Catena-X 협동로봇 (EDC + AAS) PoC

GitHub `catena_X_project` 와 동일한 **모듈 구조**(`models`, `aas_mapper`, `ai_helpers`, `edc`)를 `apps/catenax/` 아래에 두었습니다.

## 파일


| 파일                      | 역할                                          |
| ----------------------- | ------------------------------------------- |
| `edc.py`                | CLI + `CobotEDCPipeline` (온보딩·AAS·EDC mock) |
| `models.py`             | Raw/Normalized 텔레메트리, EDC/AAS 데이터 클래스       |
| `aas_mapper.py`         | 전처리 + AAS Shell/Submodel 매핑                 |
| `ai_helpers.py`         | Ollama 보조 (선택)                              |
| `edc_monolith.py`       | 이전 단일 파일 PoC (참고용 보관)                       |
| `sample_telemetry.json` | 단일 객체 또는 **객체 배열** (여러 로봇)                  |


## 빠른 시작 (mock)

저장소 루트에서:

```bash
export CATENAX_STORE_DIR="$PWD/store"   # 또는 CATENAX_MOCK_DATA_DIR (호환)

python3 apps/catenax/edc.py onboard \
  --telemetry-json apps/catenax/sample_telemetry.json \
  --provider-bpn BPNL000000000001 \
  --all-records

python3 apps/catenax/edc.py list
python3 apps/catenax/edc.py export-catalog
```

- `**--all-records` 없음**: JSON 배열이면 **첫 레코드만** 처리.
- `**--all-records`**: 배열의 **모든 로봇**에 대해 온보딩.

## 대시보드 (GitHub 스타일)

`dashboard.html` + `server/catena_app.py` — **같은 `CATENAX_STORE_DIR`** 를 읽습니다.

```bash
export CATENAX_STORE_DIR="$PWD/store"
python3 server/catena_app.py --port 8080
# 브라우저: http://127.0.0.1:8080/dashboard.html
```

## 기타 서버


| 스크립트                  | 용도                                             |
| --------------------- | ---------------------------------------------- |
| `server/dashboard.py` | 경량 KPI 대시보드 (별도 포트, `CATENAX_MOCK_DATA_DIR` 등) |
| `server/app.py`       | 협동로봇 텔레메트리 수신 API (8090 등)                     |


## 환경변수 (요약)


| 변수                           | 설명                                    |
| ---------------------------- | ------------------------------------- |
| `CATENAX_STORE_DIR`          | mock 저장 루트 (`store/aas`, `store/edc`) |
| `CATENAX_MOCK_DATA_DIR`      | 위와 동일 역할 (기존 실험 호환)                   |
| `CATENAX_EDC_MANAGEMENT_URL` | 설정 시 실제 EDC Management API            |
| `CATENAX_AAS_BASE_URL`       | 설정 시 실제 BaSyx                         |


