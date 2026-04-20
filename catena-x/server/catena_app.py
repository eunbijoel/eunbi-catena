"""server/app.py — Catena-X 협동로봇 서버 + 대시보드 API.

엔드포인트
──────────────────────────────────────────────────────────
정적 파일
  GET  /  또는  /dashboard.html     → 대시보드 HTML

시스템
  GET  /health                       헬스체크

대시보드 API (프론트엔드 ↔ 서버)
  GET  /api/dashboard                전체 요약 (카드·차트 데이터)
  GET  /api/robots                   등록 로봇 목록
  GET  /api/robots/{robot_id}        특정 로봇 상세
  GET  /api/catalog                  EDC 카탈로그
  GET  /api/policies                 등록 정책 목록

텔레메트리 수신 (협동로봇 → 서버)
  POST /api/v1/cobot/telemetry       텔레메트리 수신·저장
  GET  /api/v1/cobot/telemetry/latest
  GET  /api/v1/cobot/telemetry?limit=N

실행:
  python3 server/app.py
  python3 server/app.py --port 8080 --store ../store
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import Counter
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

_SERVER_DIR  = Path(__file__).resolve().parent
_CATENAX_DIR = _SERVER_DIR.parent
if str(_CATENAX_DIR) not in sys.path:
    sys.path.insert(0, str(_CATENAX_DIR))

LOGGER = logging.getLogger("catenax.server")

_DEFAULT_DATA_DIR  = _SERVER_DIR / "data"
_DEFAULT_STORE_DIR = _CATENAX_DIR / "store"

REQUIRED_FIELDS = {
    "robot_id", "line_id", "station_id",
    "cycle_time_ms", "power_watts", "program_name", "status",
}
STORE_LOCK = Lock()


# ─────────────────────────────────────────────────────────────────────────────
# 경로 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    d = Path(os.environ.get("COBOT_DATA_DIR", str(_DEFAULT_DATA_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_dir() -> Path:
    return Path(os.environ.get("CATENAX_STORE_DIR", str(_DEFAULT_STORE_DIR)))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe(v: str) -> str:
    return v.replace(":", "-").replace("/", "_").replace("\\", "_")


# ─────────────────────────────────────────────────────────────────────────────
# 텔레메트리 저장소
# ─────────────────────────────────────────────────────────────────────────────

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
    filename = f"{_safe(stored_at)}_{_safe(str(event.get('robot_id','x')))}.json"
    with STORE_LOCK:
        (day_dir / filename).write_text(json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8")
        (_data_dir() / "latest.json").write_text(json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("저장: robot_id=%s", event["robot_id"])
    return {"status": "stored", "stored_at": stored_at, "file": filename, "telemetry": event}


def read_latest() -> Optional[Dict[str, Any]]:
    f = _data_dir() / "latest.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def read_recent(limit: int) -> List[Dict[str, Any]]:
    files = sorted(_data_dir().glob("*/*.json"), reverse=True)
    out = []
    for p in files[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AAS / EDC 스토어 읽기
# ─────────────────────────────────────────────────────────────────────────────

def _jload(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _all_submodels() -> List[Dict[str, Any]]:
    d = _store_dir() / "aas"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*_submodel.json"))] if d.exists() else []


def _all_shells() -> List[Dict[str, Any]]:
    d = _store_dir() / "aas"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*_shell.json"))] if d.exists() else []


def _edc(filename: str) -> Dict[str, Any]:
    return _jload(_store_dir() / "edc" / filename) or {}


# ─────────────────────────────────────────────────────────────────────────────
# AAS Submodel → flat dict (프론트엔드용)
# ─────────────────────────────────────────────────────────────────────────────

def _flatten(sm: Dict[str, Any]) -> Dict[str, Any]:
    """AAS Submodel 계층 구조 → 프론트엔드 친화적 flat dict."""

    def props(elements: List[Dict]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for e in elements:
            mt = e.get("modelType", "")
            if mt == "Property":
                k, v, t = e["idShort"], e.get("value", ""), e.get("valueType", "xs:string")
                try:
                    out[k] = float(v) if t in ("xs:double", "xs:float") else (
                             int(v)   if t == "xs:integer" else v)
                except (ValueError, TypeError):
                    out[k] = v
            elif mt == "SubmodelElementCollection":
                out[e["idShort"]] = props(e.get("value", []))
        return out

    sec: Dict[str, Any] = {}
    for smc in sm.get("submodelElements", []):
        sec[smc["idShort"]] = props(smc.get("value", []))

    op   = sec.get("OperationalState", {})
    prod = sec.get("ProductionMetrics", {})
    kin  = sec.get("KinematicState", {})
    qual = sec.get("QualityAndDiagnostics", {})

    good   = int(prod.get("GoodParts", 0) or 0)
    reject = int(prod.get("RejectParts", 0) or 0)
    total  = good + reject
    yr     = prod.get("YieldRate", round(good / total, 4) if total else 0.0)

    alarms_raw = qual.get("Alarms", "none")
    alarms_list: List[str] = []
    if isinstance(alarms_raw, list):
        alarms_list = [str(x) for x in alarms_raw]
    elif isinstance(alarms_raw, str) and alarms_raw.strip() and alarms_raw.strip() != "none":
        alarms_list = [s.strip() for s in alarms_raw.split(",") if s.strip()]

    return {
        "robot_id":       op.get("RobotId", ""),
        "line_id":        op.get("LineId", ""),
        "station_id":     op.get("StationId", ""),
        "status":         op.get("Status", "UNKNOWN"),
        "program_name":   op.get("ProgramName", ""),
        "produced_at":    op.get("ProducedAt", ""),
        "cycle_time_ms":  prod.get("CycleTimeMs", 0),
        "power_watts":    prod.get("PowerWatts", 0),
        "good_parts":     good,
        "reject_parts":   reject,
        "yield_rate":     yr,
        "temperature_c":  prod.get("TemperatureC"),
        "vibration_mm_s": prod.get("VibrationMmPerSec"),
        "pose":           kin.get("EndEffectorPose", {}),
        "joint_positions": kin.get("JointPositionsDeg", {}),
        "quality_flag":   qual.get("QualityFlag", "UNKNOWN"),
        "alarm_count":    int(qual.get("AlarmCount", 0) or 0),
        "alarms":         alarms_raw if isinstance(alarms_raw, str) else ", ".join(alarms_list),
        "alarms_list":    alarms_list,
        "issues":         [v for k, v in qual.items() if k.startswith("Issue_")],
        "preprocessed_at": qual.get("PreprocessedAt", ""),
        "submodel_id":    sm.get("id", ""),
        "updated_at":     sm.get("_meta", {}).get("updated_at", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard API 응답 빌더
# ─────────────────────────────────────────────────────────────────────────────

def _joint_chart_series(robots: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    """대시보드 조인트 차트: 기본 ``cobot-01`` 이 있으면 사용, 없으면 첫 로봇."""
    prefer_id = "cobot-01"
    pick = next((r for r in robots if r.get("robot_id") == prefer_id), None)
    if pick is None and robots:
        pick = robots[0]
    if not pick:
        return [], ""
    joints = pick.get("joint_positions") or {}
    rows = [{"joint": k, "deg": float(v)} for k, v in sorted(joints.items())]
    return rows, str(pick.get("robot_id", ""))


def _build_dashboard() -> Dict[str, Any]:
    robots  = [_flatten(sm) for sm in _all_submodels()]
    catalog = list(_edc("catalog.json").values())

    status_counts: Dict[str, int] = {}
    flag_counts:   Dict[str, int] = {}
    good_total = reject_total = 0
    temps: List[float] = []
    powers: List[float] = []
    line_counts: Counter[str] = Counter()
    robots_with_alarms = 0

    for r in robots:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
        f = r["quality_flag"]
        flag_counts[f] = flag_counts.get(f, 0) + 1
        good_total   += r.get("good_parts", 0)
        reject_total += r.get("reject_parts", 0)
        if r.get("temperature_c") is not None:
            temps.append(r["temperature_c"])
        if r.get("power_watts"):
            powers.append(r["power_watts"])
        lid = str(r.get("line_id") or "").strip()
        if lid:
            line_counts[lid] += 1
        al = r.get("alarms_list")
        if isinstance(al, list) and len(al) > 0:
            robots_with_alarms += 1
        elif isinstance(r.get("alarms"), str) and r["alarms"] not in ("", "none", "None"):
            robots_with_alarms += 1

    total_p    = good_total + reject_total
    fleet_yield = round(good_total / total_p * 100, 2) if total_p else 0.0

    joint_rows, joint_robot_id = _joint_chart_series(robots)
    line_labels = sorted(line_counts.keys())

    return {
        "generated_at": _utc_now(),
        "summary": {
            "robot_count":        len(robots),
            "status_counts":      status_counts,
            "quality_counts":     flag_counts,
            "fleet_yield_pct":    fleet_yield,
            "avg_temp_c":         round(sum(temps) / len(temps), 1) if temps else None,
            "avg_power_w":        round(sum(powers) / len(powers), 1) if powers else None,
            "total_good_parts":   good_total,
            "total_reject_parts": reject_total,
            "catalog_count":      len(catalog),
            "line_count":         len(line_counts),
            "robots_with_alarms": robots_with_alarms,
        },
        "robots":  robots,
        "catalog": catalog,
        "chart_data": {
            "yield_by_robot": [
                {"robot_id": r["robot_id"], "yield_pct": round(r.get("yield_rate", 0) * 100, 2)}
                for r in robots
            ],
            "power_by_robot": [
                {"robot_id": r["robot_id"], "power_w": r.get("power_watts", 0)}
                for r in robots
            ],
            "temp_by_robot": [
                {"robot_id": r["robot_id"], "temp_c": r.get("temperature_c")}
                for r in robots if r.get("temperature_c") is not None
            ],
            "cycle_time_by_robot": [
                {"robot_id": r["robot_id"], "cycle_ms": r.get("cycle_time_ms", 0)}
                for r in robots
            ],
            "status_pie": [{"status": k, "count": v} for k, v in status_counts.items()],
            "joint_positions": joint_rows,
            "joint_chart_robot_id": joint_robot_id,
            "robots_by_line": [{"line_id": k, "robot_count": line_counts[k]} for k in line_labels],
            "vibration_by_robot": [
                {"robot_id": r["robot_id"], "vib": r.get("vibration_mm_s")}
                for r in robots
                if r.get("vibration_mm_s") is not None
            ],
        },
    }


def _build_robot_detail(robot_id: str) -> Optional[Dict[str, Any]]:
    safe_sm    = re.sub(r"[^\w\-]", "_", f"urn:cobot:sm:{robot_id}")
    safe_shell = re.sub(r"[^\w\-]", "_", f"urn:cobot:shell:{robot_id}")
    sm    = _jload(_store_dir() / "aas" / f"{safe_sm}_submodel.json")
    shell = _jload(_store_dir() / "aas" / f"{safe_shell}_shell.json")
    if not sm:
        return None

    asset_id = f"urn:catenax:cobot:{robot_id}:telemetry"
    assets   = _edc("assets.json")
    catalog  = _edc("catalog.json")
    policies = _edc("policies.json")

    return {
        "robot":      _flatten(sm),
        "shell":      shell,
        "submodel":   sm,
        "edc_asset":  assets.get(asset_id),
        "catalog":    catalog.get(asset_id),
        "policies": {
            "access":   policies.get(f"{asset_id}-access"),
            "contract": policies.get(f"{asset_id}-contract"),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 핸들러
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryHandler(BaseHTTPRequestHandler):
    server_version = "CatenaXCobotServer/3.0"

    def _json(self, status: HTTPStatus, body: Any) -> None:
        data = json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _html(self, data: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0"))
        if n == 0:
            raise ValueError("빈 요청 본문")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path   = parsed.path

        # Static dashboard
        if path in ("/", "/dashboard.html"):
            p = _CATENAX_DIR / "dashboard.html"
            self._html(p.read_bytes()) if p.exists() else self._json(
                HTTPStatus.NOT_FOUND, {"error": "dashboard.html 없음"})
            return

        if path == "/health":
            self._json(HTTPStatus.OK, {
                "status": "ok", "timestamp": _utc_now(),
                "robots_registered": len(_all_submodels()),
                "store_dir": str(_store_dir()),
            })
            return

        if path == "/api/dashboard":
            self._json(HTTPStatus.OK, _build_dashboard())
            return

        if path == "/api/robots":
            robots = [_flatten(sm) for sm in _all_submodels()]
            self._json(HTTPStatus.OK, {"count": len(robots), "robots": robots})
            return

        if path.startswith("/api/robots/"):
            rid    = path.split("/api/robots/", 1)[1].strip("/")
            detail = _build_robot_detail(rid)
            if detail is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"robot '{rid}' not found"})
            else:
                self._json(HTTPStatus.OK, detail)
            return

        if path == "/api/catalog":
            c = list(_edc("catalog.json").values())
            self._json(HTTPStatus.OK, {"count": len(c), "entries": c})
            return

        if path == "/api/policies":
            p_list = list(_edc("policies.json").values())
            self._json(HTTPStatus.OK, {"count": len(p_list), "policies": p_list})
            return

        if path == "/api/v1/cobot/telemetry/latest":
            lat = read_latest()
            self._json(HTTPStatus.OK if lat else HTTPStatus.NOT_FOUND,
                       lat or {"error": "없음"})
            return

        if path == "/api/v1/cobot/telemetry":
            qs = parse_qs(parsed.query)
            try:
                limit = max(1, min(int(qs.get("limit", ["20"])[0]), 500))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "limit는 정수여야 합니다."})
                return
            items = read_recent(limit)
            self._json(HTTPStatus.OK, {"count": len(items), "items": items})
            return

        self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/v1/cobot/telemetry":
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown path"})
            return
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        errs = validate_telemetry(payload)
        if errs:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "validation failed", "details": errs})
            return
        body = store_telemetry(payload)
        try:
            from apps import telemetry_db as _tdb

            _tdb.maybe_mirror_sqlite_after_file_store(
                payload,
                client_ip=self.client_address[0],
                request_id=self.headers.get("X-Request-Id"),
            )
        except Exception:
            LOGGER.exception("telemetry_db 미러 호출 실패")
        self._json(HTTPStatus.CREATED, body)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.address_string(), fmt % args)


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────

def run_server(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), TelemetryHandler)
    LOGGER.info("Catena-X Server  →  http://%s:%d", host, port)
    LOGGER.info("대시보드: http://%s:%d/dashboard.html", host, port)
    LOGGER.info("API:      http://%s:%d/api/dashboard", host, port)
    httpd.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Catena-X 협동로봇 서버")
    parser.add_argument("--host",  default=os.environ.get("COBOT_SERVER_HOST", "0.0.0.0"))
    parser.add_argument("--port",  type=int, default=int(os.environ.get("COBOT_SERVER_PORT", "8080")))
    parser.add_argument("--store", default=None, help="AAS/EDC 스토어 경로")
    args = parser.parse_args()
    if args.store:
        os.environ["CATENAX_STORE_DIR"] = str(Path(args.store).resolve())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s — %(message)s")
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
