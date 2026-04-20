-- 팀 ER 다이어그램 기준 — PostgreSQL 예시 DDL
-- 로컬 검증: psql "$DATABASE_URL" -f sql/postgres_cobot_telemetry.sql
-- 앱에서 쓰는 컬럼 이름은 apps/telemetry_db.py 의 상수와 맞춤

BEGIN;

CREATE TABLE IF NOT EXISTS cobot_telemetry_raw (
    id               BIGSERIAL PRIMARY KEY,
    event_id         TEXT        NOT NULL UNIQUE,
    robot_id         TEXT        NOT NULL,
    line_id          TEXT        NOT NULL,
    station_id       TEXT        NOT NULL,
    produced_at      TIMESTAMPTZ NOT NULL,
    payload          JSONB       NOT NULL,
    schema_version   TEXT        NOT NULL DEFAULT '1',
    received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ip        TEXT,
    content_type     TEXT        NOT NULL DEFAULT 'application/json',
    request_id       TEXT,
    checksum_sha256  TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cobot_raw_received ON cobot_telemetry_raw (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_cobot_raw_robot ON cobot_telemetry_raw (robot_id);

CREATE TABLE IF NOT EXISTS cobot_measurements (
    id               BIGSERIAL PRIMARY KEY,
    event_id         TEXT        NOT NULL REFERENCES cobot_telemetry_raw (event_id) ON DELETE CASCADE,
    robot_id         TEXT        NOT NULL,
    line_id          TEXT        NOT NULL,
    station_id       TEXT        NOT NULL,
    produced_at      TIMESTAMPTZ NOT NULL,
    cycle_time_ms    DOUBLE PRECISION NOT NULL,
    power_watts      DOUBLE PRECISION NOT NULL,
    program_name     TEXT        NOT NULL,
    status           TEXT        NOT NULL,
    good_parts       INTEGER     NOT NULL DEFAULT 0,
    reject_parts     INTEGER     NOT NULL DEFAULT 0,
    temperature_c    DOUBLE PRECISION,
    vibration_mm_s   DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_cobot_m_robot_time ON cobot_measurements (robot_id, produced_at DESC);

CREATE TABLE IF NOT EXISTS cobot_aas_sync_status (
    event_id     TEXT        NOT NULL PRIMARY KEY REFERENCES cobot_telemetry_raw (event_id) ON DELETE CASCADE,
    robot_id     TEXT        NOT NULL,
    sync_status  TEXT        NOT NULL DEFAULT 'Pending',
    last_error   TEXT,
    synced_at    TIMESTAMPTZ,
    retry_count  INTEGER     NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cobot_telemetry_latest (
    robot_id     TEXT        NOT NULL PRIMARY KEY,
    line_id      TEXT        NOT NULL,
    station_id   TEXT        NOT NULL,
    produced_at  TIMESTAMPTZ NOT NULL,
    payload      JSONB       NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cobot_access_audit (
    id               BIGSERIAL PRIMARY KEY,
    event_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_type       TEXT        NOT NULL,
    actor_id         TEXT,
    action           TEXT        NOT NULL,
    target_resource  TEXT        NOT NULL,
    result           TEXT        NOT NULL,
    correlation_id   TEXT,
    details          JSONB
);

CREATE INDEX IF NOT EXISTS idx_cobot_audit_time ON cobot_access_audit (event_time DESC);

COMMIT;
