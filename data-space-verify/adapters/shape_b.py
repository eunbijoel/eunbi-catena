"""형태 B: 다른 컬럼명(line_snapshots 스타일) 행 → 동일 표준 JSON.

데이터는 ``fixtures/shape_b_rows.json`` 에서 읽습니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from canonical import canonical_telemetry

_ROOT = Path(__file__).resolve().parents[1]
_ROWS = _ROOT / "fixtures" / "shape_b_rows.json"


def iter_canonical_records() -> List[Dict[str, Any]]:
    raw = json.loads(_ROWS.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("shape_b_rows.json must be a JSON array")
    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        st = str(row["status_label"])
        out.append(
            canonical_telemetry(
                robot_id=str(row["device_id"]),
                line_id=str(row["line"]),
                station_id=str(row["station_id"]),
                cycle_time_ms=float(row["cycle_time_ms"]),
                power_watts=float(row["power_w"]),
                program_name=str(row["program"]),
                status="RUNNING" if st == "OK" else st,
                good_parts=int(row["gp"]),
                reject_parts=int(row["rp"]),
                temperature_c=float(row["temp"]) if row.get("temp") is not None else None,
                vibration_mm_s=float(row["vibration"]) if row.get("vibration") is not None else None,
                produced_at=str(row["ts"]),
            )
        )
    return out
