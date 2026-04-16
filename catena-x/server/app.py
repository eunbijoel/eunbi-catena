#!/usr/bin/env python3
"""협동로봇 텔레메트리 HTTP API (표준 라이브러리만).

엔드포인트 (edc.py 파이프라인과 동일 경로 권장)
    POST   /api/v1/cobot/telemetry   — raw JSON 수신 → 메모리 버퍼 + (옵션) 파일
    GET    /api/v1/cobot/telemetry/latest?robot_id=
    GET    /api/v1/cobot/telemetry   — 최근 N건
    GET    /api/v1/aas/robot/<robot_id> — (옵션) 로컬 AAS JSON 저장소 조회
    GET    /api/v1/catalog          — 정적/모의 카탈로그 메타

실운영: uvicorn/gunicorn + FastAPI 로 교체하고, 본 핸들러 로직은 ``apps.catenax.edc`` 의
``ingest_raw_to_aas`` / ``AASJsonRepository`` 를 호출하도록 연결하면 됩니다.

환경변수
    COBOT_API_HOST (기본 0.0.0.0)
    COBOT_API_PORT (기본 8090)
    COBOT_DATA_DIR   텔레메트리 JSONL 저장 디렉터리 (선택)
    CATENAX_MOCK_DATA_DIR  AAS JSON 조회 시 사용 (선택, GET /aas 와 연동)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# 프로젝트 루트를 path에 추가 (python server/app.py 실행 시)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LOGGER = logging.getLogger("cobot.api")

# 메모리 버퍼: 최근 레코드 (robot_id 별 latest 용)
_LATEST: Dict[str, Dict[str, Any]] = {}
_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=500)


def _json_response(handler: BaseHTTPRequestHandler, code: int, body: Any) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class CobotTelemetryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/api/v1/cobot/telemetry/latest":
            rid = (qs.get("robot_id") or [""])[0]
            if not rid or rid not in _LATEST:
                _json_response(self, 404, {"error": "not_found", "robot_id": rid})
                return
            _json_response(self, 200, _LATEST[rid])
            return

        if path == "/api/v1/cobot/telemetry":
            n = int((qs.get("limit") or ["50"])[0])
            items = list(_BUFFER)[-n:]
            _json_response(self, 200, {"items": items, "count": len(items)})
            return

        if path.startswith("/api/v1/aas/robot/"):
            rid = path.split("/api/v1/aas/robot/", 1)[-1]
            store = Path(os.environ.get("CATENAX_MOCK_DATA_DIR", "./data/catena_mock")) / "store" / "aas" / rid.replace("/", "_")
            out: Dict[str, Any] = {"robot_id": rid}
            for name in ("submodel.json", "shell.json", "meta.json"):
                p = store / name
                if p.is_file():
                    with open(p, encoding="utf-8") as f:
                        out[name.replace(".json", "")] = json.load(f)
            if len(out) <= 1:
                _json_response(self, 404, {"error": "aas_not_found", "robot_id": rid})
                return
            _json_response(self, 200, out)
            return

        if path == "/api/v1/catalog":
            _json_response(
                self,
                200,
                {
                    "note": "정적 메타. 실제 EDC Federated Catalog 는 Management /v3/catalog/request",
                    "endpointRef": "edc:connector",
                },
            )
            return

        if path in {"/", "/health"}:
            _json_response(self, 200, {"status": "ok", "service": "cobot-telemetry-api"})
            return

        _json_response(self, 404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path != "/api/v1/cobot/telemetry":
            _json_response(self, 404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"error": "invalid_json", "detail": str(exc)})
            return

        rid = str(payload.get("robot_id", ""))
        if rid:
            _LATEST[rid] = payload
        rec = {"robot_id": rid, "stored": True}
        _BUFFER.append(payload)

        data_dir = os.environ.get("COBOT_DATA_DIR", "")
        if data_dir:
            p = Path(data_dir)
            p.mkdir(parents=True, exist_ok=True)
            logf = p / "telemetry.jsonl"
            with open(logf, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            rec["appended_to"] = str(logf)

        _json_response(self, 201, {"accepted": True, **rec})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    host = os.environ.get("COBOT_API_HOST", "0.0.0.0")
    port = int(os.environ.get("COBOT_API_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), CobotTelemetryHandler)
    LOGGER.info("Cobot telemetry API http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
