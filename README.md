# eunbi

이 저장소는 여러 실험을 담는 워크스페이스입니다.

| 경로 | 설명 |
|------|------|
| **[catena-x/](catena-x/README.md)** | **Catena-X 협동로봇 PoC** — EDC/AAS mock, CLI, 대시보드 |
| [labs/](labs/) | Catena-X와 무관한 메모·가이드 (OpenClaw, RAG 등) |
| `catena_X_project/` | 별도 Git 클론 (상위 `.gitignore`로 커밋 제외) |

### 대시보드 — 저장소 **루트**에서 (예전 경로 대체)

정리 후 `server/` 는 **`catena-x/server/`** 로만 있습니다. 루트에서 예전처럼 켜려면:

```bash
cd /path/to/eunbi
chmod +x run_dashboard.sh   # 최초 1회
./run_dashboard.sh --port 8765
```

브라우저: **http://127.0.0.1:8765/dashboard.html**

같은 일을 직접 하려면:

```bash
export CATENAX_STORE_DIR="$PWD/catena-x/store"
python3 catena-x/server/catena_app.py --port 8765
```

Catena-X CLI·문서는 **`catena-x/`** 기준이 맞고, `run_dashboard.sh` 는 실행 위치만 맞춰 줍니다.
