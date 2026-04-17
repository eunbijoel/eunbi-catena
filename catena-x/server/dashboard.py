#!/usr/bin/env python3
"""Catena-X COBOT DATA SPACE — PoC 웹 대시보드 (표준 라이브러리만).

``apps/catenax/edc.py`` mock 파이프라인과 동일 ``CATENAX_MOCK_DATA_DIR`` 를 읽어
KPI·플릿·알람을 표시하고, **Run Pipeline** 으로 샘플 텔레메트리 ingest 를 실행합니다.

실행::

    cd /path/to/eunbi/catena-x
    export CATENAX_MODE=mock
    python3 server/dashboard.py

브라우저: http://127.0.0.1:8765/

환경변수: DASHBOARD_HOST (기본 0.0.0.0), DASHBOARD_PORT (기본 8765),
          CATENAX_MOCK_DATA_DIR (기본 ``<catena-x>/data/catena_mock``)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LOGGER = logging.getLogger("catena.dashboard")

DEFAULT_MOCK = _ROOT / "data" / "catena_mock"
SAMPLE_JSON = _ROOT / "apps" / "catenax" / "sample_telemetry.json"


def mock_dir() -> Path:
    return Path(os.environ.get("CATENAX_MOCK_DATA_DIR", str(DEFAULT_MOCK)))


def _walk_properties(elements: Any) -> Generator[Tuple[str, Any], None, None]:
    if not isinstance(elements, list):
        return
    for el in elements:
        if not isinstance(el, dict):
            continue
        mt = el.get("modelType")
        if mt == "Property":
            yield str(el.get("idShort", "")), el.get("value")
        elif mt == "SubmodelElementCollection":
            yield from _walk_properties(el.get("value"))


def _robot_id_from_submodel(sub: Dict[str, Any]) -> str:
    """GitHub 스타일 flat submodel JSON 에서 robot_id 추출."""
    for smc in sub.get("submodelElements") or []:
        if smc.get("idShort") == "OperationalState":
            for el in smc.get("value") or []:
                if el.get("idShort") == "RobotId" and el.get("modelType") == "Property":
                    return str(el.get("value", "unknown"))
    return "unknown"


def _metrics_from_submodel(sub: Dict[str, Any]) -> Dict[str, Any]:
    """AAS Submodel JSON 에서 KPI 추출 (PoC)."""
    out: Dict[str, Any] = {
        "status": "—",
        "good_parts": 0,
        "reject_parts": 0,
        "power_watts": 0.0,
        "cycle_time_ms": 0.0,
        "alarms_json": "[]",
    }
    for k, v in _walk_properties(sub.get("submodelElements")):
        if k == "Status":
            out["status"] = str(v)
        elif k == "GoodParts":
            try:
                out["good_parts"] = int(float(v))
            except (TypeError, ValueError):
                pass
        elif k == "RejectParts":
            try:
                out["reject_parts"] = int(float(v))
            except (TypeError, ValueError):
                pass
        elif k == "PowerWatts":
            try:
                out["power_watts"] = float(v)
            except (TypeError, ValueError):
                pass
        elif k == "CycleTimeMs":
            try:
                out["cycle_time_ms"] = float(v)
            except (TypeError, ValueError):
                pass
        elif k == "Alarms":
            out["alarms_json"] = str(v)
    return out


def _sample_file_robot_stats() -> Dict[str, Any]:
    """``sample_telemetry.json`` 레코드 수·서로 다른 robot_id 개수."""
    try:
        from apps.catenax import edc

        raw = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
        recs = edc._iter_telemetry_records(raw)
        ids = [str(r.get("robot_id", "")) for r in recs]
        return {
            "sample_total_records": len(recs),
            "sample_unique_robots": len(set(ids)),
        }
    except Exception as exc:  # noqa: BLE001
        return {"sample_error": str(exc)}


def aggregate_store() -> Dict[str, Any]:
    """``store/aas/<robot>`` 및 edc mock 파일에서 요약."""
    root = mock_dir()
    aas_base = root / "store" / "aas"
    robots: List[Dict[str, Any]] = []
    total_good = 0
    total_rej = 0
    running_n = 0
    warn_robots = 0
    alarm_rows: List[Dict[str, str]] = []

    if aas_base.is_dir():
        for d in sorted(aas_base.iterdir()):
            if not d.is_dir():
                continue
            sm_path = d / "submodel.json"
            meta_path = d / "meta.json"
            rid = d.name
            if meta_path.is_file():
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    rid = str(meta.get("robot_id", rid))
                except (OSError, json.JSONDecodeError):
                    pass
            m: Dict[str, Any] = {"robot_id": rid, "folder": d.name}
            if sm_path.is_file():
                try:
                    with open(sm_path, encoding="utf-8") as f:
                        sub = json.load(f)
                    mm = _metrics_from_submodel(sub)
                    m.update(mm)
                    total_good += int(mm.get("good_parts", 0))
                    total_rej += int(mm.get("reject_parts", 0))
                    st = str(mm.get("status", "")).upper()
                    if st == "RUNNING":
                        running_n += 1
                    if st in {"ERROR", "STOP"}:
                        warn_robots += 1
                    try:
                        alarms = json.loads(mm.get("alarms_json", "[]"))
                        if isinstance(alarms, list):
                            if alarms:
                                warn_robots += 1
                            for a in alarms[:5]:
                                alarm_rows.append({"robot": rid, "text": str(a)})
                    except json.JSONDecodeError:
                        pass
                except (OSError, json.JSONDecodeError) as exc:
                    LOGGER.warning("read %s: %s", sm_path, exc)
            robots.append(m)

    # GitHub 모듈 스타일: store/aas/*_submodel.json (폴더 없이 파일만)
    if not robots and aas_base.is_dir():
        for sm_path in sorted(aas_base.glob("*_submodel.json")):
            try:
                with open(sm_path, encoding="utf-8") as f:
                    sub = json.load(f)
                rid = _robot_id_from_submodel(sub)
                mm = _metrics_from_submodel(sub)
                m = {"robot_id": rid, "folder": sm_path.name}
                m.update(mm)
                total_good += int(mm.get("good_parts", 0))
                total_rej += int(mm.get("reject_parts", 0))
                st = str(mm.get("status", "")).upper()
                if st == "RUNNING":
                    running_n += 1
                if st in {"ERROR", "STOP"}:
                    warn_robots += 1
                try:
                    alarms = json.loads(mm.get("alarms_json", "[]"))
                    if isinstance(alarms, list) and alarms:
                        warn_robots += 1
                    if isinstance(alarms, list):
                        for a in alarms[:5]:
                            alarm_rows.append({"robot": rid, "text": str(a)})
                except json.JSONDecodeError:
                    pass
                robots.append(m)
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("read %s: %s", sm_path, exc)

    n = len(robots)
    total_parts = total_good + total_rej
    defect_rate = (total_rej / total_parts) if total_parts > 0 else 0.0
    vals = [float(r.get("power_watts", 0)) for r in robots]
    lp_avg = {"ALL": sum(vals) / len(vals) if vals else 0.0}

    assets_n = 0
    for ap in (root / "edc_assets.json", root / "store" / "edc" / "assets.json"):
        if ap.is_file():
            try:
                with open(ap, encoding="utf-8") as f:
                    data = json.load(f)
                assets_n = len(data) if isinstance(data, dict) else len(data)
                break
            except (OSError, json.JSONDecodeError):
                pass

    samp = _sample_file_robot_stats()
    su = samp.get("sample_unique_robots")
    sync_hint: Optional[str] = None
    if isinstance(su, int) and su > 0 and n < su:
        sync_hint = (
            f"JSON 샘플에는 서로 다른 로봇이 {su}대인데, AAS에 반영된 건 {n}대로 보입니다. "
            "`onboard --all-records` 또는 **Run Pipeline** 으로 배열 전체를 다시 넣으세요."
        )

    return {
        "total_robots": n,
        "running": running_n,
        "warnings_errors": warn_robots,
        "total_good_parts": total_good,
        "defect_rate": round(defect_rate, 4),
        "line_power_avg": lp_avg,
        "robots": robots[:50],
        "alarms": alarm_rows[:20],
        "edc_assets_registered": assets_n,
        "mock_dir": str(root.resolve()),
        **samp,
        "sync_hint": sync_hint,
    }


def run_sample_pipeline() -> Dict[str, Any]:
    """edc mock: 샘플 JSON → 모듈형 ``onboard`` (배열이면 전체)."""
    os.environ["CATENAX_MODE"] = "mock"
    d = mock_dir()
    d.mkdir(parents=True, exist_ok=True)
    store_root = d / "store"
    store_root.mkdir(parents=True, exist_ok=True)
    os.environ["CATENAX_MOCK_DATA_DIR"] = str(d)
    os.environ["CATENAX_STORE_DIR"] = str(store_root)

    from apps.catenax import edc

    raw = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
    records = edc._iter_telemetry_records(raw)
    pipe = edc.build_pipeline_from_env(ai_disabled=True)
    provider = os.environ.get("CATENAX_PROVIDER_BPN", "BPNL000000000001")
    out = edc.run_onboard_from_records(
        pipe,
        records,
        provider_bpn=provider,
        cobot_api_base_url=os.environ.get("COBOT_API_BASE", "http://127.0.0.1:8090"),
    )
    hist = d / "pipeline_history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        snap = {"ok": True, "record_count": out.get("record_count")}
        res = out.get("results")
        if isinstance(res, list):
            snap["robot_ids"] = [r.get("robot_id") for r in res if isinstance(r, dict)]
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return {"ok": True, "result": out}


def run_onboard_demo() -> Dict[str, Any]:
    """레거시 데모 슬롯 — ``run_sample_pipeline`` 과 동일 동작."""
    return run_sample_pipeline()


HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Catena-X COBOT DATA SPACE</title>
  <style>
    :root {
      --bg: #0f172a; --panel: #1e293b; --accent: #14b8a6; --text: #e2e8f0;
      --muted: #94a3b8; --border: #334155; --warn: #f59e0b; --err: #ef4444;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text);
      min-height: 100vh; display: flex; }
    nav {
      width: 220px; background: var(--panel); border-right: 1px solid var(--border);
      padding: 1rem 0; flex-shrink: 0;
    }
    nav h1 { font-size: 0.75rem; color: var(--muted); padding: 0 1rem; margin: 0 0 1rem; letter-spacing: .05em; }
    nav section { margin-bottom: 1.25rem; }
    nav .sec { font-size: 0.65rem; color: var(--muted); padding: 0.25rem 1rem; text-transform: uppercase; }
    nav a {
      display: block; padding: 0.45rem 1rem; color: var(--text); text-decoration: none; font-size: 0.9rem;
    }
    nav a:hover, nav a.active { background: rgba(20,184,166,.15); border-left: 3px solid var(--accent); }
    main { flex: 1; padding: 1.25rem 1.5rem; overflow: auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    header h2 { margin: 0; font-size: 1.25rem; }
    .sub { color: var(--muted); font-size: 0.85rem; margin-top: 0.25rem; }
    .actions { display: flex; gap: 0.5rem; }
    button {
      background: var(--accent); color: #0f172a; border: none; padding: 0.5rem 1rem;
      border-radius: 6px; font-weight: 600; cursor: pointer;
    }
    button.secondary { background: var(--panel); color: var(--text); border: 1px solid var(--border); }
    button:disabled { opacity: 0.5; cursor: wait; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .card {
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1rem;
    }
    .card .label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }
    .card .val { font-size: 1.5rem; font-weight: 700; margin-top: 0.25rem; }
    .card .hint { font-size: 0.75rem; color: var(--muted); margin-top: 0.35rem; }
    .panel-lg { grid-column: 1 / -1; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; }
    .badge { display: inline-block; padding: 0.15rem 0.45rem; border-radius: 4px; font-size: 0.75rem; background: #334155; }
    .toast { margin-top: 1rem; padding: 0.75rem; border-radius: 8px; background: #134e4a; font-size: 0.85rem; display: none; }
    .toast.err { background: #450a0a; }
    .hint-banner {
      display: none; margin-bottom: 1rem; padding: 0.85rem 1rem; border-radius: 8px;
      background: #422006; border: 1px solid var(--warn); color: #fef3c7; font-size: 0.88rem; line-height: 1.45;
    }
    .hint-banner.show { display: block; }
    footer { margin-top: 2rem; font-size: 0.75rem; color: var(--muted); }
  </style>
</head>
<body>
  <nav>
    <h1>Catena-X COBOT DATA SPACE</h1>
    <section>
      <div class="sec">Overview</div>
      <a href="#" class="active">Dashboard</a>
      <a href="#">Robot Fleet <span class="badge" id="nav-fleet">—</span></a>
      <a href="#">Telemetry</a>
    </section>
    <section>
      <div class="sec">Pipeline</div>
      <a href="#">AI Pipeline</a>
      <a href="#">AAS Submodels</a>
      <a href="#">Validation</a>
    </section>
    <section>
      <div class="sec">EDC</div>
      <a href="#">Connector</a>
      <a href="#">Catalog</a>
    </section>
  </nav>
  <main>
    <header>
      <div>
        <h2>공장 개요</h2>
        <div class="sub">Catena-X 데이터 공간 · 협동로봇 실시간 현황 (PoC)</div>
      </div>
      <div class="actions">
        <button type="button" class="secondary" id="btn-refresh">Refresh</button>
        <button type="button" id="btn-pipeline">Run Pipeline</button>
      </div>
    </header>
    <div id="hint-banner" class="hint-banner"></div>
    <div class="grid" id="kpi">
      <div class="card"><div class="label">총 로봇</div><div class="val" id="k-total">—</div><div class="hint">등록된 cobot (AAS 스토어)</div></div>
      <div class="card"><div class="label">가동 중</div><div class="val" id="k-run">—</div><div class="hint">RUNNING 상태</div></div>
      <div class="card"><div class="label">경고/오류</div><div class="val" id="k-warn">—</div><div class="hint">알람·ERROR/STOP</div></div>
      <div class="card"><div class="label">양품 합계</div><div class="val" id="k-good">—</div><div class="hint">누적 good parts</div></div>
      <div class="card"><div class="label">불량률</div><div class="val" id="k-def">—</div><div class="hint">reject / (good+reject)</div></div>
      <div class="card"><div class="label">EDC 자산</div><div class="val" id="k-edc">—</div><div class="hint">mock 등록 건수</div></div>
    </div>
    <div class="card panel-lg">
      <div class="label">라인별 평균 전력 (W)</div>
      <div class="val" id="k-power" style="font-size:1.1rem;margin-top:.5rem">—</div>
    </div>
    <div class="card panel-lg">
      <div class="label">최근 알람</div>
      <table><thead><tr><th>로봇</th><th>알람</th></tr></thead><tbody id="alarm-body"><tr><td colspan="2">데이터 로딩 중…</td></tr></tbody></table>
    </div>
    <div class="card panel-lg">
      <div class="label">Robot Fleet (AAS)</div>
      <table><thead><tr><th>robot_id</th><th>status</th><th>good</th><th>reject</th><th>power W</th></tr></thead><tbody id="fleet-body"><tr><td colspan="5">—</td></tr></tbody></table>
    </div>
    <div id="toast" class="toast"></div>
    <footer>mock_dir: <span id="mock-path">—</span> · API <code>/api/summary</code> <code>POST /api/pipeline/run</code></footer>
  </main>
  <script>
    async function loadSummary() {
      const r = await fetch('/api/summary');
      const d = await r.json();
      document.getElementById('k-total').textContent = d.total_robots ?? '0';
      document.getElementById('k-run').textContent = d.running ?? '0';
      document.getElementById('k-warn').textContent = d.warnings_errors ?? '0';
      document.getElementById('k-good').textContent = d.total_good_parts ?? '0';
      document.getElementById('k-def').textContent = (d.defect_rate != null) ? (d.defect_rate * 100).toFixed(2) + '%' : '—';
      document.getElementById('k-edc').textContent = d.edc_assets_registered ?? '0';
      document.getElementById('k-power').textContent = JSON.stringify(d.line_power_avg || {});
      document.getElementById('nav-fleet').textContent = d.total_robots ?? '0';
      document.getElementById('mock-path').textContent = d.mock_dir || '—';
      const hb = document.getElementById('hint-banner');
      if (d.sync_hint) {
        hb.textContent = d.sync_hint;
        hb.className = 'hint-banner show';
      } else {
        hb.textContent = '';
        hb.className = 'hint-banner';
      }
      const ab = document.getElementById('alarm-body');
      ab.innerHTML = '';
      if (!d.alarms || !d.alarms.length) {
        ab.innerHTML = '<tr><td colspan="2">알람 없음</td></tr>';
      } else {
        d.alarms.forEach(a => {
          ab.innerHTML += '<tr><td>' + (a.robot || '') + '</td><td>' + (a.text || '') + '</td></tr>';
        });
      }
      const fb = document.getElementById('fleet-body');
      fb.innerHTML = '';
      if (!d.robots || !d.robots.length) {
        fb.innerHTML = '<tr><td colspan="5">AAS 스토어 비어 있음 — Run Pipeline 실행</td></tr>';
      } else {
        d.robots.forEach(x => {
          fb.innerHTML += '<tr><td>' + (x.robot_id||'') + '</td><td>' + (x.status||'—') + '</td><td>' + (x.good_parts??'—') +
            '</td><td>' + (x.reject_parts??'—') + '</td><td>' + (x.power_watts??'—') + '</td></tr>';
        });
      }
    }
    function showToast(msg, isErr) {
      const t = document.getElementById('toast');
      t.style.display = 'block';
      t.textContent = msg;
      t.className = 'toast' + (isErr ? ' err' : '');
      setTimeout(() => { t.style.display = 'none'; }, 8000);
    }
    document.getElementById('btn-refresh').onclick = () => loadSummary().catch(e => showToast(String(e), true));
    document.getElementById('btn-pipeline').onclick = async () => {
      const b = document.getElementById('btn-pipeline');
      b.disabled = true;
      try {
        const r = await fetch('/api/pipeline/run', { method: 'POST' });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
        var msg = 'Pipeline OK';
        if (j.result) {
          if (j.result.record_count) msg += ' (' + j.result.record_count + ' robots)';
          else if (j.result.aas && j.result.aas.path) msg += ': ' + j.result.aas.path;
        }
        showToast(msg);
        await loadSummary();
      } catch (e) {
        showToast(String(e), true);
      } finally {
        b.disabled = false;
      }
    };
    loadSummary().catch(e => showToast(String(e), true));
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - " + fmt, self.address_string(), *args)

    def _json(self, code: int, body: Any) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            data = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/summary":
            try:
                self._json(200, aggregate_store())
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("summary")
                self._json(500, {"error": str(exc), "trace": traceback.format_exc()})
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/pipeline/run":
            try:
                out = run_sample_pipeline()
                self._json(200, out)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("pipeline")
                self._json(500, {"ok": False, "error": str(exc), "trace": traceback.format_exc()})
            return
        if path == "/api/onboard":
            try:
                ob = run_onboard_demo()
                self._json(200, {"ok": True, "onboard": ob})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        self.send_error(404, "Not Found")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    mock_dir().mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    LOGGER.info("Catena-X dashboard http://%s:%s/ (mock: %s)", host, port, mock_dir())
    httpd.serve_forever()


if __name__ == "__main__":
    main()
