"""형태 A: 레거시 컬럼명(영문 약어) 행 → 표준 JSON.

데이터는 ``fixtures/shape_a_rows.json`` (SQL ``SELECT`` 결과와 동일한 키)에서 읽습니다.
SQLite 없이 동작해 ``_sqlite3`` 미포함 Python에서도 실행됩니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from canonical import canonical_telemetry

_ROOT = Path(__file__).resolve().parents[1]
_ROWS = _ROOT / "fixtures" / "shape_a_rows.json"


def iter_canonical_records() -> List[Dict[str, Any]]:
    raw = json.loads(_ROWS.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("shape_a_rows.json must be a JSON array")
    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        out.append(
            canonical_telemetry(
                robot_id=str(row["robot_name"]),
                line_id=str(row["assembly_line"]),
                station_id=str(row["station_code"]),
                cycle_time_ms=float(row["cycle_ms"]),
                power_watts=float(row["kw"]),
                program_name=str(row["prog_name"]),
                status=str(row["run_state"]),
                good_parts=int(row["ok_count"]),
                reject_parts=int(row["ng_count"]),
                temperature_c=float(row["temp_c"]) if row.get("temp_c") is not None else None,
                vibration_mm_s=float(row["vib"]) if row.get("vib") is not None else None,
                produced_at=str(row["created_ts"]),
                alarms=["LEGACY-FLAG"] if str(row["run_state"]) == "WARNING" else [],
            )
        )
    return out
