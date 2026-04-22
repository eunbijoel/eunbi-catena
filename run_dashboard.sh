#!/usr/bin/env bash
# Catena-X 대시보드 — 저장소 루트(eunbi)에서 예전처럼 실행할 때 사용.
# 사용: ./run_dashboard.sh --port 8765
# 브라우저: http://127.0.0.1:8765/dashboard.html

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
export CATENAX_STORE_DIR="${CATENAX_STORE_DIR:-$REPO_ROOT/catena-x/store}"
# 인자에 --port 가 없으면 README 의 8765 와 동일하게 (예전처럼 ./run_dashboard.sh 만 쳐도 열림)
has_port=false
for a in "$@"; do
  if [[ "$a" == "--port" ]] || [[ "$a" == --port=* ]]; then has_port=true; break; fi
done
if ! $has_port; then
  set -- --port 8765 "$@"
fi
exec python3 "$REPO_ROOT/catena-x/server/catena_app.py" "$@"
