"""팀 SQL 스키마(ER)와 맞춘 텔레메트리 영속화 헬퍼.

- **PostgreSQL**: ``sql/postgres_cobot_telemetry.sql`` DDL 적용 후 환경 변수
  ``COBOT_DATABASE_URL`` (또는 ``DATABASE_URL``) 에 연결 문자열을 두면
  ``maybe_mirror_after_file_store`` 가 동일 테이블·순서로 기록합니다. 드라이버는
  ``pip install -r requirements.txt`` (``psycopg2-binary``).
- **SQLite (로컬 미러)**: ``COBOT_TELEMETRY_DB`` 에 파일 경로 — 기존과 동일.

Postgres와 SQLite를 **둘 다** 설정하면 수신 시 순서대로 둘 다 미러합니다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("catenax.telemetry_db")

# 테이블 이름 — SQL 파일과 동일
T_RAW = "cobot_telemetry_raw"
T_MEAS = "cobot_measurements"
T_LATEST = "cobot_telemetry_latest"
T_SYNC = "cobot_aas_sync_status"
T_AUDIT = "cobot_access_audit"

DEFAULT_SCHEMA_VERSION = "1"


def new_event_id() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def checksum_payload_sha256(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


_SQLITE_DDL = f"""
CREATE TABLE IF NOT EXISTS {T_RAW} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    robot_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    produced_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1',
    received_at TEXT NOT NULL,
    source_ip TEXT,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    request_id TEXT,
    checksum_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cobot_raw_received ON {T_RAW} (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_cobot_raw_robot ON {T_RAW} (robot_id);

CREATE TABLE IF NOT EXISTS {T_MEAS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    robot_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    produced_at TEXT NOT NULL,
    cycle_time_ms REAL NOT NULL,
    power_watts REAL NOT NULL,
    program_name TEXT NOT NULL,
    status TEXT NOT NULL,
    good_parts INTEGER NOT NULL DEFAULT 0,
    reject_parts INTEGER NOT NULL DEFAULT 0,
    temperature_c REAL,
    vibration_mm_s REAL
);
CREATE INDEX IF NOT EXISTS idx_cobot_m_robot_time ON {T_MEAS} (robot_id, produced_at DESC);

CREATE TABLE IF NOT EXISTS {T_SYNC} (
    event_id TEXT NOT NULL PRIMARY KEY,
    robot_id TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'Pending',
    last_error TEXT,
    synced_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {T_LATEST} (
    robot_id TEXT NOT NULL PRIMARY KEY,
    line_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    produced_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {T_AUDIT} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    target_resource TEXT NOT NULL,
    result TEXT NOT NULL,
    correlation_id TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_cobot_audit_time ON {T_AUDIT} (event_time DESC);
"""


def ensure_sqlite_schema(conn: Any) -> None:
    conn.executescript(_SQLITE_DDL)
    conn.commit()


def _parse_ts(s: Optional[str]) -> str:
    if not s:
        return _utc_now_iso()
    return str(s)


def ingest_http_payload_sqlite(
    db_path: str | Path,
    payload: Dict[str, Any],
    *,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    actor_type: str = "Service",
    actor_id: str = "catena_app",
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """HTTP로 받은 텔레메트리 한 건을 SQLite에 ER과 동일 흐름으로 기록.

    Returns:
        ``{ "event_id", "checksum_sha256" }`` — AAS 동기화 후 ``mark_aas_sync_sqlite`` 등에 사용.
    """
    import sqlite3 as _sqlite3

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    event_id = event_id or new_event_id()
    received_at = _utc_now_iso()
    produced_at = _parse_ts(payload.get("produced_at"))  # type: ignore[arg-type]
    checksum = checksum_payload_sha256(payload)
    payload_json = json.dumps(payload, ensure_ascii=False)
    robot_id = str(payload["robot_id"])
    line_id = str(payload["line_id"])
    station_id = str(payload["station_id"])

    conn = _sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        ensure_sqlite_schema(conn)

        conn.execute(
            f"""
            INSERT INTO {T_RAW} (
                event_id, robot_id, line_id, station_id, produced_at, payload,
                schema_version, received_at, source_ip, content_type, request_id, checksum_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                robot_id,
                line_id,
                station_id,
                produced_at,
                payload_json,
                schema_version,
                received_at,
                client_ip,
                "application/json",
                request_id,
                checksum,
            ),
        )

        conn.execute(
            f"""
            INSERT INTO {T_MEAS} (
                event_id, robot_id, line_id, station_id, produced_at,
                cycle_time_ms, power_watts, program_name, status,
                good_parts, reject_parts, temperature_c, vibration_mm_s
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                robot_id,
                line_id,
                station_id,
                produced_at,
                float(payload["cycle_time_ms"]),
                float(payload["power_watts"]),
                str(payload["program_name"]),
                str(payload["status"]),
                int(payload.get("good_parts", 0)),
                int(payload.get("reject_parts", 0)),
                float(payload["temperature_c"]) if payload.get("temperature_c") is not None else None,
                float(payload["vibration_mm_s"]) if payload.get("vibration_mm_s") is not None else None,
            ),
        )

        conn.execute(
            f"""
            INSERT INTO {T_LATEST} (robot_id, line_id, station_id, produced_at, payload, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(robot_id) DO UPDATE SET
                line_id=excluded.line_id,
                station_id=excluded.station_id,
                produced_at=excluded.produced_at,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (robot_id, line_id, station_id, produced_at, payload_json, received_at),
        )

        conn.execute(
            f"""
            INSERT INTO {T_SYNC} (event_id, robot_id, sync_status, last_error, synced_at, retry_count, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (event_id, robot_id, "Pending", None, None, 0, received_at),
        )

        audit_details = json.dumps({"event_id": event_id, "robot_id": robot_id}, ensure_ascii=False)
        conn.execute(
            f"""
            INSERT INTO {T_AUDIT} (
                event_time, actor_type, actor_id, action, target_resource, result, correlation_id, details
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                received_at,
                actor_type,
                actor_id,
                "Ingest",
                f"telemetry:{robot_id}",
                "Success",
                request_id,
                audit_details,
            ),
        )

        conn.commit()
    finally:
        conn.close()

    LOGGER.info("telemetry_db: SQLite ingest event_id=%s robot_id=%s", event_id, robot_id)
    return {"event_id": event_id, "checksum_sha256": checksum}


def mark_aas_sync_sqlite(
    db_path: str | Path,
    event_id: str,
    *,
    sync_status: str,
    last_error: Optional[str] = None,
) -> None:
    """AAS 반영 후 상태 갱신 — ``edc`` 파이프라인 끝에서 호출 예정."""
    import sqlite3 as _sqlite3

    received_at = _utc_now_iso()
    synced_at = received_at if sync_status == "Synced" else None
    conn = _sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute(
            f"""
            UPDATE {T_SYNC}
            SET sync_status=?, last_error=?, synced_at=?, updated_at=?
            WHERE event_id=?
            """,
            (sync_status, last_error, synced_at, received_at, event_id),
        )
        if conn.total_changes == 0:
            LOGGER.warning("telemetry_db: no sync row for event_id=%s", event_id)
        conn.commit()
    finally:
        conn.close()


def _normalize_postgres_dsn(dsn: str) -> str:
    d = dsn.strip()
    if d.startswith("postgres://"):
        return "postgresql://" + d[len("postgres://") :]
    return d


def _postgres_dsn_from_env() -> str:
    return (
        os.environ.get("COBOT_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )


def ingest_http_payload_postgres(
    dsn: str,
    payload: Dict[str, Any],
    *,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    actor_type: str = "Service",
    actor_id: str = "catena_app",
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """HTTP 텔레메트리 한 건을 PostgreSQL에 기록 — ``postgres_cobot_telemetry.sql`` 스키마."""
    import psycopg2
    from psycopg2.extras import Json

    dsn_n = _normalize_postgres_dsn(dsn)
    event_id = event_id or new_event_id()
    received_at = _utc_now_iso()
    produced_at = _parse_ts(payload.get("produced_at"))  # type: ignore[arg-type]
    checksum = checksum_payload_sha256(payload)
    robot_id = str(payload["robot_id"])
    line_id = str(payload["line_id"])
    station_id = str(payload["station_id"])

    conn = psycopg2.connect(dsn_n)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {T_RAW} (
                    event_id, robot_id, line_id, station_id, produced_at, payload,
                    schema_version, received_at, source_ip, content_type, request_id, checksum_sha256
                ) VALUES (%s,%s,%s,%s,%s::timestamptz,%s,%s,%s::timestamptz,%s,%s,%s,%s)
                """,
                (
                    event_id,
                    robot_id,
                    line_id,
                    station_id,
                    produced_at,
                    Json(payload),
                    schema_version,
                    received_at,
                    client_ip,
                    "application/json",
                    request_id,
                    checksum,
                ),
            )

            cur.execute(
                f"""
                INSERT INTO {T_MEAS} (
                    event_id, robot_id, line_id, station_id, produced_at,
                    cycle_time_ms, power_watts, program_name, status,
                    good_parts, reject_parts, temperature_c, vibration_mm_s
                ) VALUES (%s,%s,%s,%s,%s::timestamptz,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    event_id,
                    robot_id,
                    line_id,
                    station_id,
                    produced_at,
                    float(payload["cycle_time_ms"]),
                    float(payload["power_watts"]),
                    str(payload["program_name"]),
                    str(payload["status"]),
                    int(payload.get("good_parts", 0)),
                    int(payload.get("reject_parts", 0)),
                    float(payload["temperature_c"]) if payload.get("temperature_c") is not None else None,
                    float(payload["vibration_mm_s"]) if payload.get("vibration_mm_s") is not None else None,
                ),
            )

            cur.execute(
                f"""
                INSERT INTO {T_LATEST} (robot_id, line_id, station_id, produced_at, payload, updated_at)
                VALUES (%s,%s,%s,%s::timestamptz,%s,%s::timestamptz)
                ON CONFLICT (robot_id) DO UPDATE SET
                    line_id = EXCLUDED.line_id,
                    station_id = EXCLUDED.station_id,
                    produced_at = EXCLUDED.produced_at,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (robot_id, line_id, station_id, produced_at, Json(payload), received_at),
            )

            cur.execute(
                f"""
                INSERT INTO {T_SYNC} (event_id, robot_id, sync_status, last_error, synced_at, retry_count, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::timestamptz)
                """,
                (event_id, robot_id, "Pending", None, None, 0, received_at),
            )

            audit_details = {"event_id": event_id, "robot_id": robot_id}
            cur.execute(
                f"""
                INSERT INTO {T_AUDIT} (
                    event_time, actor_type, actor_id, action, target_resource, result, correlation_id, details
                ) VALUES (%s::timestamptz,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    received_at,
                    actor_type,
                    actor_id,
                    "Ingest",
                    f"telemetry:{robot_id}",
                    "Success",
                    request_id,
                    Json(audit_details),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    LOGGER.info("telemetry_db: Postgres ingest event_id=%s robot_id=%s", event_id, robot_id)
    return {"event_id": event_id, "checksum_sha256": checksum}


def mark_aas_sync_postgres(
    dsn: str,
    event_id: str,
    *,
    sync_status: str,
    last_error: Optional[str] = None,
) -> None:
    """AAS 반영 후 ``cobot_aas_sync_status`` 갱신."""
    import psycopg2

    received_at = _utc_now_iso()
    synced_at = received_at if sync_status == "Synced" else None
    conn = psycopg2.connect(_normalize_postgres_dsn(dsn))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {T_SYNC}
                SET sync_status=%s, last_error=%s, synced_at=%s::timestamptz, updated_at=%s::timestamptz
                WHERE event_id=%s
                """,
                (sync_status, last_error, synced_at, received_at, event_id),
            )
            if cur.rowcount == 0:
                LOGGER.warning("telemetry_db: Postgres no sync row for event_id=%s", event_id)
        conn.commit()
    finally:
        conn.close()


def maybe_mirror_after_file_store(
    payload: Dict[str, Any],
    *,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """파일 저장 후: ``COBOT_DATABASE_URL``/``DATABASE_URL`` 이면 Postgres, ``COBOT_TELEMETRY_DB`` 이면 SQLite."""
    dsn = _postgres_dsn_from_env()
    db_path = os.environ.get("COBOT_TELEMETRY_DB", "").strip()
    shared_event_id: Optional[str] = None
    if dsn and db_path:
        shared_event_id = new_event_id()

    if dsn:
        try:
            ingest_http_payload_postgres(
                dsn,
                dict(payload),
                client_ip=client_ip,
                request_id=request_id,
                event_id=shared_event_id,
            )
        except ImportError:
            LOGGER.warning(
                "Postgres 미러 건너뜀: psycopg2 없음. ``pip install -r requirements.txt`` 후 재시도."
            )
        except Exception as exc:
            LOGGER.warning("Postgres 미러 실패(디스크 저장은 유지): %s", exc)

    if not db_path:
        return
    try:
        ingest_http_payload_sqlite(
            db_path,
            dict(payload),
            client_ip=client_ip,
            request_id=request_id,
            event_id=shared_event_id,
        )
    except Exception as exc:
        LOGGER.warning("COBOT_TELEMETRY_DB 미러 실패(디스크 저장은 유지): %s", exc)


def maybe_mirror_sqlite_after_file_store(
    payload: Dict[str, Any],
    *,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """호환 별칭 — ``maybe_mirror_after_file_store`` 와 동일."""
    maybe_mirror_after_file_store(
        payload,
        client_ip=client_ip,
        request_id=request_id,
    )
