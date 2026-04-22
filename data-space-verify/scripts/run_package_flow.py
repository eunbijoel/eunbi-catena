#!/usr/bin/env python3
"""서로 다른 'SQL 행' 형태(JSON fixture) → 표준 JSON → ``catena-x/apps/edc.py onboard`` → store 산출물 요약.

행 데이터는 ``fixtures/*_rows.json`` (컬럼명이 DB와 동일한 배열)에서 읽습니다. SQLite 미포함 Python에서도 동작합니다.
``CATENAX_STORE_DIR`` 은 매 실행 임시 디렉터리로 격리합니다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATENA = REPO_ROOT.parent / "catena-x"


def _summarize_store(store_dir: Path) -> Dict[str, Any]:
    edc = store_dir / "edc"
    aas = store_dir / "aas"
    out: Dict[str, Any] = {"edc_dir": str(edc), "aas_glob": 0}
    if not edc.is_dir():
        return {**out, "error": "no edc dir"}
    for name in ("assets.json", "policies.json", "contracts.json", "catalog.json"):
        p = edc / name
        if not p.is_file():
            out[name] = None
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out[name] = len(data)
            else:
                out[name] = len(list(data))
        except (OSError, json.JSONDecodeError):
            out[name] = "?"
    if aas.is_dir():
        out["aas_glob"] = len(list(aas.glob("*.json")))
    return out


def _run_onboard(catena_root: Path, json_path: Path, store_dir: Path, provider_bpn: str, no_ai: bool) -> Tuple[int, str]:
    env = {**os.environ, "CATENAX_STORE_DIR": str(store_dir)}
    cmd = [
        sys.executable,
        "apps/edc.py",
        "onboard",
        "--telemetry-json",
        str(json_path.resolve()),
        "--provider-bpn",
        provider_bpn,
    ]
    if no_ai:
        cmd.append("--no-ai")
    proc = subprocess.run(
        cmd,
        cwd=str(catena_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    return proc.returncode, tail


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catena-root", type=Path, default=DEFAULT_CATENA)
    p.add_argument("--provider-bpn", default="BPNL000000000001")
    p.add_argument("--no-ai", action="store_true", help="edc.py onboard 시 Ollama 생략")
    args = p.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    from adapters import ADAPTERS

    catena = args.catena_root.resolve()
    if not (catena / "apps" / "edc.py").is_file():
        print(f"catena-x 없음: {catena}", file=sys.stderr)
        return 2

    store_dir = Path(tempfile.mkdtemp(prefix="dsv-package-store-"))
    print(f"CATENAX_STORE_DIR = {store_dir}")

    failed = 0
    total = 0
    for adapter_name, loader in ADAPTERS:
        print(f"\n--- 어댑터: {adapter_name} ---")
        try:
            records: List[Dict[str, Any]] = loader()
        except Exception as exc:
            print(f"  [NG] 로드 실패: {exc}")
            failed += 1
            continue
        for i, payload in enumerate(records):
            total += 1
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
            ) as tf:
                json.dump(payload, tf, ensure_ascii=False)
                tmp = Path(tf.name)
            try:
                code, tail = _run_onboard(catena, tmp, store_dir, args.provider_bpn, args.no_ai)
                rid = payload.get("robot_id", "")
                if code == 0:
                    print(f"  [OK] onboard row {i} robot_id={rid!r}")
                else:
                    failed += 1
                    print(f"  [NG] onboard row {i} robot_id={rid!r} exit={code}\n{tail}")
            finally:
                tmp.unlink(missing_ok=True)

    print("\n=== 패키지 산출물 (EDC mock + AAS 파일 개수) ===")
    summary = _summarize_store(store_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if failed:
        print(f"\n요약: 실패 {failed} / 총 onboard 시도 {total}")
        return 1
    print(f"\n요약: 전부 성공 (onboard {total}건, store는 위 경로 — 필요 시 수동 삭제)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
