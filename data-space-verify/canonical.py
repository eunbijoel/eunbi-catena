"""서로 다른 'SQL 행' 형태(레거시 컬럼명 등) → catena-x 가 받는 **표준 텔레메트리 JSON** 한 객체."""

from __future__ import annotations

from typing import Any, Dict


def canonical_telemetry(
    *,
    robot_id: str,
    line_id: str,
    station_id: str,
    cycle_time_ms: float,
    power_watts: float,
    program_name: str,
    status: str,
    good_parts: int = 0,
    reject_parts: int = 0,
    temperature_c: float | None = None,
    vibration_mm_s: float | None = None,
    produced_at: str,
    alarms: list[str] | None = None,
) -> Dict[str, Any]:
    """`server/app.py` 필수 필드 + `RawTelemetry.from_dict` 가 기대하는 키."""
    out: Dict[str, Any] = {
        "robot_id": robot_id,
        "line_id": line_id,
        "station_id": station_id,
        "cycle_time_ms": float(cycle_time_ms),
        "power_watts": float(power_watts),
        "program_name": program_name,
        "status": status,
        "good_parts": int(good_parts),
        "reject_parts": int(reject_parts),
        "produced_at": produced_at,
        "pose": {},
        "joint_positions_deg": {},
        "alarms": list(alarms or []),
    }
    if temperature_c is not None:
        out["temperature_c"] = float(temperature_c)
    if vibration_mm_s is not None:
        out["vibration_mm_s"] = float(vibration_mm_s)
    return out
