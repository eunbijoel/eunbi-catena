#!/usr/bin/env python3
"""협동로봇 텔레메트리 수신 서버 (Catena-X / EDC HttpData 소스용).

흐름
──────────────────────────────────────────────────────────
POST /api/v1/cobot/telemetry  → validate_telemetry() → 실패 시 400
                             → 성공 시 store_telemetry()
                                 · data/YYYY-MM-DD/<timestamp>_<robot>.json
                                 · data/latest.json 갱신
                             → 201 + stored 메타

GET  /api/v1/cobot/telemetry/latest   → 최신 1건 (latest.json 또는 ?robot_id=)
GET  /api/v1/cobot/telemetry?limit=N   → 일별 파일에서 최근 N건
GET  /health

실행 예 (BerePi / 센터장님 워크플로와 동일 구조)::

    cd catena-x
    python3 server/app.py --host 0.0.0.0 --port 8080

환경변수
    COBOT_DATA_DIR   기본: <이 파일>/data  (즉 server/data/)
    COBOT_API_HOST / COBOT_API_PORT  — argparse 없이 환경만 쓸 때
    CATENAX_STORE_DIR  GET /api/v1/aas/robot/<id> 조회 시 mock AAS 경로 (기본 catena-x/store)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

_SERVER_DIR = Path(__file__).resolve().parent
_ROOT = _SERVER_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LOGGER = logging.getLogger("cobot.telemetry.server")

REQUIRED_FIELDS = {
    "robot_id",
    "line_id",
    "station_id",
    "cycle_time_ms",
    "power_watts",
    "program_name",
    "status",
}

STORE_LOCK = Lock()
# robot_id → 최신 페이로드 (GET latest?robot_id= 용)
_LATEST_BY_ROBOT: Dict[str, Dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _data_dir() -> Path:
    d = Path(os.environ.get("COBOT_DATA_DIR", str(_SERVER_DIR / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(v: str) -> str:
    return v.replace(":", "-").replace("/", "_").replace("\\", "_")


def validate_telemetry(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    missing = sorted(f for f in REQUIRED_FIELDS if f not in payload)
    if missing:
        errors.append(f"필수 필드 누락: {', '.join(missing)}")
        return errors
    for f in ("cycle_time_ms", "power_watts"):
        try:
            float(payload[f])
        except (TypeError, ValueError):
            errors.append(f"'{f}' 는 숫자여야 합니다.")
    return errors


def store_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
    stored_at = _utc_now()
    event = {**payload, "stored_at": stored_at}
    day_dir = _data_dir() / datetime.now(UTC).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe(stored_at)}_{_safe(str(event.get('robot_id', 'x')))}.json"
    rid = str(event.get("robot_id", ""))
    with STORE_LOCK:
        (day_dir / filename).write_text(
            json.dumps(event, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (_data_dir() / "latest.json").write_text(
            json.dumps(event, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if rid:
            _LATEST_BY_ROBOT[rid] = event
    LOGGER.info("저장: robot_id=%s file=%s", rid, filename)
    return {
        "status": "stored",
        "stored_at": stored_at,
        "file": filename,
        "day_dir": str(day_dir.name),
        "telemetry": event,
    }


def read_latest_file() -> Optional[Dict[str, Any]]:
    f = _data_dir() / "latest.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_recent(limit: int) -> List[Dict[str, Any]]:
    files = sorted(_data_dir().glob("*/*.json"), reverse=True)
    out: List[Dict[str, Any]] = []
    for p in files:
        if p.name == "latest.json":
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if len(out) >= limit:
            break
    return out


def _store_dir_aas() -> Path:
    base = Path(os.environ.get("CATENAX_STORE_DIR", str(_ROOT / "store")))
    return base / "aas"


class CobotTelemetryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.address_string(), fmt % args)

    def _json(self, code: int, body: Any) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path in ("/health", "/"):
            self._json(
                200,
                {"status": "ok", "service": "cobot-telemetry-api", "data_dir": str(_data_dir())},
            )
            return

        if path == "/api/v1/cobot/telemetry/latest":
            rid = (qs.get("robot_id") or [""])[0].strip()
            if rid:
                if rid not in _LATEST_BY_ROBOT:
                    self._json(404, {"error": "not_found", "robot_id": rid})
                    return
                self._json(200, _LATEST_BY_ROBOT[rid])
                return
            latest = read_latest_file()
            if latest is None:
                self._json(404, {"error": "no_telemetry_yet"})
                return
            self._json(200, latest)
            return

        if path == "/api/v1/cobot/telemetry":
            try:
                limit = max(1, min(int((qs.get("limit") or ["20"])[0]), 500))
            except ValueError:
                self._json(400, {"error": "limit must be integer"})
                return
            items = read_recent(limit)
            self._json(200, {"count": len(items), "items": items})
            return

        if path.startswith("/api/v1/aas/robot/"):
            rid = path.split("/api/v1/aas/robot/", 1)[-1]
            base = _store_dir_aas()
            # 레거시 폴더형 + GitHub 스타일 flat 파일
            legacy = base / rid.replace("/", "_")
            sm: Optional[Dict[str, Any]] = None
            if (legacy / "submodel.json").is_file():
                sm = json.loads((legacy / "submodel.json").read_text(encoding="utf-8"))
            else:
                for p in sorted(base.glob("*_submodel.json")):
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        # 간단히 robot id 매칭 (OperationalState.RobotId)
                        blob = json.dumps(data, ensure_ascii=False)
                        if rid in blob:
                            sm = data
                            break
                    except (OSError, json.JSONDecodeError):
                        continue
            if sm is None:
                self._json(404, {"error": "aas_not_found", "robot_id": rid})
                return
            self._json(200, {"robot_id": rid, "submodel": sm})
            return

        if path == "/api/v1/catalog":
            self._json(
                200,
                {
                    "note": "EDC Federated Catalog 는 Connector Management /v3/catalog/request",
                    "endpointRef": "edc:connector",
                },
            )
            return

        self._json(404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path != "/api/v1/cobot/telemetry":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            self._json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        errs = validate_telemetry(payload)
        if errs:
            self._json(400, {"error": "validation_failed", "details": errs})
            return

        body = store_telemetry(payload)
        self._json(201, body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cobot telemetry HTTP server")
    parser.add_argument("--host", default=os.environ.get("COBOT_API_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("COBOT_API_PORT", "8080")),
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="COBOT_DATA_DIR 로 쓸 경로 (기본: server/data/)",
    )
    args = parser.parse_args()
    if args.data_dir:
        os.environ["COBOT_DATA_DIR"] = str(Path(args.data_dir).resolve())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    httpd = ThreadingHTTPServer((args.host, args.port), CobotTelemetryHandler)
    LOGGER.info("Cobot telemetry API → http://%s:%s", args.host, args.port)
    LOGGER.info("  POST /api/v1/cobot/telemetry  ·  data: %s", _data_dir())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("종료")


if __name__ == "__main__":
    main()
