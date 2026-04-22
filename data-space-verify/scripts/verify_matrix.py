#!/usr/bin/env python3
"""시나리오 JSON으로 catena-x 검증·온보딩 스모크 매트릭스.

- pass/*.json  → validate 통과 + (옵션) edc.py onboard exit 0
- fail_validate/*.json → validate 에러 비어 있지 않음 (거절 기대)

catena-x 소스는 수정하지 않고 subprocess / importlib 로만 호출합니다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT.parent
DEFAULT_CATENA = WORKSPACE / "catena-x"
SCENARIOS = REPO_ROOT / "scenarios"


def _load_app_validate(catena_root: Path):
    spec = importlib.util.spec_from_file_location("cobot_app", catena_root / "server" / "app.py")
    if not spec or not spec.loader:
        raise RuntimeError("app.py 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate_telemetry


def _json_files(d: Path) -> List[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def _run_onboard(catena_root: Path, fixture: Path, provider_bpn: str, no_ai: bool) -> Tuple[int, str]:
    tmp = tempfile.mkdtemp(prefix="catena-verify-store-")
    env = {**os.environ, "CATENAX_STORE_DIR": tmp}
    cmd = [
        sys.executable,
        "apps/edc.py",
        "onboard",
        "--telemetry-json",
        str(fixture.resolve()),
        "--provider-bpn",
        provider_bpn,
    ]
    if no_ai:
        cmd.append("--no-ai")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(catena_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catena-root", type=Path, default=DEFAULT_CATENA, help="catena-x 루트 (기본: 형제 디렉터리)")
    p.add_argument("--skip-onboard", action="store_true", help="onboard 생략 (검증만)")
    p.add_argument("--no-ai", action="store_true", help="edc.py onboard 에 --no-ai 전달")
    p.add_argument("--provider-bpn", default="BPNL000000000001")
    args = p.parse_args()

    catena = args.catena_root.resolve()
    if not (catena / "apps" / "edc.py").is_file():
        print(f"catena-x 를 찾을 수 없습니다: {catena}", file=sys.stderr)
        return 2

    validate = _load_app_validate(catena)
    passed = 0
    failed = 0

    def check_validate(label: str, path: Path, expect_errors: bool) -> None:
        nonlocal passed, failed
        payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        errs = validate(payload)
        ok = bool(errs) if expect_errors else not errs
        if ok:
            passed += 1
            print(f"  [OK] validate {label}: {path.name}")
        else:
            failed += 1
            print(f"  [NG] validate {label}: {path.name} errs={errs!r}")

    print("=== fail_validate (검증 거절 기대) ===")
    for path in _json_files(SCENARIOS / "fail_validate"):
        check_validate("expect_fail", path, expect_errors=True)

    print("=== pass (검증 통과 기대) ===")
    for path in _json_files(SCENARIOS / "pass"):
        check_validate("expect_ok", path, expect_errors=False)

    if not args.skip_onboard:
        print("=== pass + onboard (격리 CATENAX_STORE_DIR) ===")
        for path in _json_files(SCENARIOS / "pass"):
            code, tail = _run_onboard(catena, path, args.provider_bpn, args.no_ai)
            if code == 0:
                passed += 1
                print(f"  [OK] onboard {path.name}")
            else:
                failed += 1
                print(f"  [NG] onboard {path.name} exit={code}\n{tail}")

    print(f"\n요약: OK={passed}  NG={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
