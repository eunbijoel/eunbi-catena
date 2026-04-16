# eunbi — Catena-X cobot PoC

## Catena-X 파이프라인 (권장)

모듈화된 구현은 `**apps/catenax/**` 를 보세요.  
자세한 CLI·대시보드·환경변수는 `[apps/catenax/README.md](apps/catenax/README.md)` 참고.

**한 줄 요약**

```bash
export CATENAX_STORE_DIR="$PWD/store"
python3 apps/catenax/edc.py onboard \
  --telemetry-json apps/catenax/sample_telemetry.json \
  --provider-bpn BPNL000000000001 --all-records
python3 server/catena_app.py --port 8080
# → http://127.0.0.1:8080/dashboard.html
```

로컬 mock 데이터는 기본적으로 `store/` 아래에 생깁니다 (`.gitignore`에 `store/` 권장).