"""edc.py — Catena-X 협동로봇 데이터 플랫폼 핵심 엔트리포인트.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
프로젝트 Objective / Goal / Non-Goal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Objective
    공장 협동로봇 텔레메트리를 Catena-X 데이터스페이스 표준(EDC + AAS)으로
    등록·관리·공유할 수 있는 샘플 플랫폼을 구현한다.

Goal
    ✔ raw telemetry → 전처리 → AAS Submodel 저장/갱신
    ✔ EDC 에셋/정책/컨트랙트를 로컬 저장소에 등록
    ✔ 로컬 카탈로그 조회 (export-catalog)
    ✔ Ollama AI 보조 검증 (기본 시도, Ollama 없으면 스킵 / --no-ai 로 끔)
    ✔ sample_telemetry.json으로 즉시 테스트 가능
    ✔ Mock ↔ 실제 EDC/BaSyx 전환 경계 명확화

Non-Goal
    ✘ 실제 EDC 컨트랙트 협상·데이터 전송 (Mock 수준)
    ✘ AAS 레지스트리 원격 동기화 (Mock 수준)
    ✘ 멀티 테넌시, 인증/인가 (샘플 수준)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
전체 데이터 흐름 (6단계)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  sample_telemetry.json
         │
         ▼  [Step 1] raw data mapping
  RawTelemetry
         │
         ▼  [Step 2] 전처리 (임계값 검사, 품질 플래그, 수율 계산)
  NormalizedTelemetry ──[Step 5]──→ Ollama AI 검증 (가능하면 자동, 실패 시 스킵)
         │
         ▼  [Step 3] AAS 매핑 (IDTA-02017 SubmodelElementCollection 구조)
  AASShell + AASSubmodel
         │
         ├──[Step 4]──→ EDCAsset + EDCPolicy + ContractDefinition
         │               store/edc/assets.json
         │               store/edc/policies.json
         │               store/edc/contracts.json
         │               store/edc/catalog.json
         │
         └──[Step 6]──→ AAS upsert (INSERT or UPDATE)
                         store/aas/{robot_id}_shell.json
                         store/aas/{robot_id}_submodel.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mock ↔ Real 전환 경계
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  EDCStore  → 로컬 JSON 파일 (Mock)
              실제 연동: CATENAX_EDC_MANAGEMENT_URL 설정 시 EDCHttpClient 자동 활성화
  AASStore  → 로컬 JSON 파일 (Mock)
              실제 연동: CATENAX_AAS_BASE_URL 설정 시 BaSyxAASClient 자동 활성화
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── 패키지 경로 보정 ─────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from models import (
    AASShell,
    AASStoreError,
    AASSubmodel,
    CatalogEntry,
    ContractDefinition,
    EDCAsset,
    EDCPolicy,
    EDCStoreError,
    InvalidTelemetryError,
    NormalizedTelemetry,
    QualityFlag,
    RawTelemetry,
)
from aas_mapper import AASMapper, TelemetryPreprocessor, TelemetryThresholds

# Ollama AI 보조 — import 실패해도 시스템 동작 보장
try:
    from ai_helpers import (
        OllamaUnavailableError,
        check_ollama_available,
        suggest_policy_with_ai,
        validate_with_ai,
    )
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False

LOGGER = logging.getLogger("catenax.edc")


def _ai_disabled_by_env() -> bool:
    """``CATENAX_DISABLE_AI=1`` (또는 true/yes) 이면 Ollama 단계를 건너뜀."""
    v = os.environ.get("CATENAX_DISABLE_AI", "").strip().lower()
    return v in ("1", "true", "yes")


# ─────────────────────────────────────────────────────────────────────────────
# 경로/유틸
# ─────────────────────────────────────────────────────────────────────────────

def _store_root() -> Path:
    # GitHub 기본: CATENAX_STORE_DIR — 기존 로컬 실험과 호환: CATENAX_MOCK_DATA_DIR 도 허용
    base = os.environ.get("CATENAX_STORE_DIR") or os.environ.get("CATENAX_MOCK_DATA_DIR")
    d = Path(base or str(_HERE / "store"))
    (d / "aas").mkdir(parents=True, exist_ok=True)
    (d / "edc").mkdir(parents=True, exist_ok=True)
    return d


def _iter_telemetry_records(raw: Any) -> List[Dict[str, Any]]:
    """이미 파싱된 JSON(객체 또는 배열)에서 레코드 리스트 — 대시보드·레거시 호환."""
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        out: List[Dict[str, Any]] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"텔레메트리 배열 요소 {i}는 객체여야 합니다.")
            out.append(item)
        if not out:
            raise ValueError("텔레메트리 JSON 배열이 비어 있습니다.")
        return out
    raise ValueError(f"텔레메트리는 객체 또는 배열이어야 합니다: {type(raw).__name__}")


def _load_telemetry_records(path: Path) -> List[Dict[str, Any]]:
    """파일에서 JSON 읽어 ``_iter_telemetry_records`` 로 정규화."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _iter_telemetry_records(raw)


def run_onboard_from_records(
    pipeline: CobotEDCPipeline,
    records: List[Dict[str, Any]],
    *,
    provider_bpn: str,
    cobot_api_base_url: str = "http://localhost:8080",
    cobot_data_path: str = "/api/v1/cobot/telemetry",
    policy_type: str = "bpn",
    edc_asset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """레코드마다 ``onboard`` 실행 — 예전 ``ingest_*`` 대체."""
    results: List[Dict[str, Any]] = []
    for raw in records:
        results.append(
            pipeline.onboard(
                raw_dict=raw,
                provider_bpn=provider_bpn,
                cobot_api_base_url=cobot_api_base_url,
                cobot_data_path=cobot_data_path,
                policy_type=policy_type,
                edc_asset_id=edc_asset_id,
            )
        )
    if len(results) == 1:
        return {"record_count": 1, "results": results, **results[0]}
    return {"record_count": len(results), "results": results}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^\w\-]", "_", value)


def _make_asset_id(robot_id: str) -> str:
    """robot_id 기반 EDC 에셋 ID 생성 규칙."""
    return f"urn:catenax:cobot:{robot_id}:telemetry"


def _make_policy(policy_id: str, target: str, policy_type: str, bpn: str) -> EDCPolicy:
    """정책 유형 문자열 → EDCPolicy 객체."""
    if policy_type == "membership":
        return EDCPolicy.membership(policy_id, target)
    if policy_type == "open":
        return EDCPolicy.open_access(policy_id, target)
    return EDCPolicy.bpn_restricted(policy_id, target, bpn)


# ═════════════════════════════════════════════════════════════════════════════
# AAS 로컬 저장소
# ═════════════════════════════════════════════════════════════════════════════

class AASStore:
    """AAS Shell과 Submodel을 로컬 JSON 파일로 저장·조회.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Mock 경계 — 실제 BaSyx AAS Server 연동 시 교체
      현재: store/aas/*.json 로컬 파일
      목표: BaSyx REST API (PUT /shells, PUT /submodels)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """

    def __init__(self, store_root: Optional[Path] = None):
        self._root = (store_root or _store_root()) / "aas"
        self._root.mkdir(parents=True, exist_ok=True)

    def upsert_shell(self, shell: AASShell) -> Dict[str, Any]:
        """Shell upsert: 없으면 INSERT, 있으면 UPDATE (created_at 보존).

        실제 BaSyx 연동 시 → POST /shells (신규) / PUT /shells/{id} (갱신)
        """
        path      = self._root / f"{_safe_filename(shell.shell_id)}_shell.json"
        is_update = path.exists()

        if is_update:
            existing       = json.loads(path.read_text(encoding="utf-8"))
            shell.created_at = existing.get("_meta", {}).get("created_at", _utc_now())
            shell.updated_at = _utc_now()

        path.write_text(json.dumps(shell.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        action = "UPDATED" if is_update else "CREATED"
        LOGGER.info("AAS Shell %s: %s", action, shell.shell_id)
        return {"action": action, "shell_id": shell.shell_id, "path": str(path)}

    def upsert_submodel(self, submodel: AASSubmodel) -> Dict[str, Any]:
        """Submodel upsert: 없으면 INSERT, 있으면 UPDATE.

        실제 BaSyx 연동 시 → POST /submodels (신규) / PUT /submodels/{id} (갱신)
        """
        path      = self._root / f"{_safe_filename(submodel.submodel_id)}_submodel.json"
        is_update = path.exists()

        if is_update:
            existing           = json.loads(path.read_text(encoding="utf-8"))
            submodel.created_at = existing.get("_meta", {}).get("created_at", _utc_now())
            submodel.updated_at = _utc_now()

        path.write_text(json.dumps(submodel.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        action = "UPDATED" if is_update else "CREATED"
        LOGGER.info("AAS Submodel %s: %s", action, submodel.submodel_id)
        return {"action": action, "submodel_id": submodel.submodel_id, "path": str(path)}

    def get_shell(self, shell_id: str) -> Optional[Dict[str, Any]]:
        path = self._root / f"{_safe_filename(shell_id)}_shell.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def get_submodel(self, submodel_id: str) -> Optional[Dict[str, Any]]:
        path = self._root / f"{_safe_filename(submodel_id)}_submodel.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_shells(self) -> List[Dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self._root.glob("*_shell.json"))]

    def list_submodels(self) -> List[Dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self._root.glob("*_submodel.json"))]


# ═════════════════════════════════════════════════════════════════════════════
# EDC 로컬 저장소
# ═════════════════════════════════════════════════════════════════════════════

class EDCStore:
    """EDC 에셋/정책/컨트랙트/카탈로그를 로컬 JSON 파일로 저장·조회.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Mock 경계 — 실제 EDC Management API 연동 시 교체
      현재: store/edc/*.json 로컬 파일
      목표: EDC Management API v3
            POST /v3/assets
            POST /v3/policydefinitions
            POST /v3/contractdefinitions
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """

    def __init__(self, store_root: Optional[Path] = None):
        self._root = (store_root or _store_root()) / "edc"
        self._root.mkdir(parents=True, exist_ok=True)

    # ── 에셋 ──────────────────────────────────────────────────────────────────

    def register_asset(self, asset: EDCAsset) -> Dict[str, Any]:
        """에셋 등록. 실제 EDC 연동 시 → POST /v3/assets"""
        store                = self._load("assets.json")
        store[asset.asset_id] = asset.to_dict()
        self._save("assets.json", store)
        LOGGER.info("EDC 에셋 등록: %s", asset.asset_id)
        return {"registered": True, "asset_id": asset.asset_id}

    def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        return self._load("assets.json").get(asset_id)

    def list_assets(self) -> List[Dict[str, Any]]:
        return list(self._load("assets.json").values())

    # ── 정책 ──────────────────────────────────────────────────────────────────

    def register_policy(self, policy: EDCPolicy) -> Dict[str, Any]:
        """정책 등록. 실제 EDC 연동 시 → POST /v3/policydefinitions"""
        store                  = self._load("policies.json")
        store[policy.policy_id] = policy.to_dict()
        self._save("policies.json", store)
        LOGGER.info("EDC 정책 등록: %s [%s]", policy.policy_id, policy.policy_type.value)
        return {"registered": True, "policy_id": policy.policy_id}

    def list_policies(self) -> List[Dict[str, Any]]:
        return list(self._load("policies.json").values())

    # ── 컨트랙트 정의 ─────────────────────────────────────────────────────────

    def register_contract(self, contract: ContractDefinition) -> Dict[str, Any]:
        """컨트랙트 등록. 실제 EDC 연동 시 → POST /v3/contractdefinitions"""
        store                                  = self._load("contracts.json")
        store[contract.contract_definition_id]  = contract.to_dict()
        self._save("contracts.json", store)
        LOGGER.info("EDC 컨트랙트 등록: %s", contract.contract_definition_id)
        return {"registered": True, "contract_id": contract.contract_definition_id}

    def list_contracts(self) -> List[Dict[str, Any]]:
        return list(self._load("contracts.json").values())

    # ── 카탈로그 ──────────────────────────────────────────────────────────────

    def upsert_catalog_entry(self, entry: CatalogEntry) -> Dict[str, Any]:
        store                  = self._load("catalog.json")
        store[entry.asset_id]  = entry.to_dict()
        self._save("catalog.json", store)
        LOGGER.info("카탈로그 갱신: %s", entry.asset_id)
        return {"updated": True, "asset_id": entry.asset_id}

    def list_catalog(self) -> List[Dict[str, Any]]:
        return list(self._load("catalog.json").values())

    # ── 파일 IO ───────────────────────────────────────────────────────────────

    def _load(self, filename: str) -> Dict[str, Any]:
        path = self._root / filename
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EDCStoreError(f"{filename} 읽기 실패: {exc}") from exc

    def _save(self, filename: str, data: Dict[str, Any]) -> None:
        path = self._root / filename
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            raise EDCStoreError(f"{filename} 쓰기 실패: {exc}") from exc


# ═════════════════════════════════════════════════════════════════════════════
# 실제 EDC HTTP 클라이언트 (환경변수 설정 시 자동 활성화)
# ═════════════════════════════════════════════════════════════════════════════

class EDCHttpClient:
    """Eclipse EDC Management API v3 HTTP 클라이언트.

    CATENAX_EDC_MANAGEMENT_URL 환경변수가 설정되면 자동 활성화됩니다.
    Mock → Real 전환: CobotEDCPipeline에 이 클라이언트를 주입하세요.

    실제 연동 예시:
        export CATENAX_EDC_MANAGEMENT_URL=http://edc-provider:8080/management
        export CATENAX_EDC_API_KEY=your-key
        python3 edc.py onboard --telemetry-json sample_telemetry.json ...
    """

    RETRYABLE_CODES = {429, 500, 502, 503, 504}

    def __init__(self, management_url: str, api_key: Optional[str] = None,
                 timeout: float = 15.0, max_retries: int = 3):
        self.management_url = management_url.rstrip("/")
        self.api_key        = api_key
        self.timeout        = timeout
        self.max_retries    = max_retries

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        url  = f"{self.management_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        req  = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8").strip()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in self.RETRYABLE_CODES and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"연결 실패 {url}: {exc.reason}") from exc
        raise RuntimeError("최대 재시도 초과")

    def register_asset(self, asset: EDCAsset) -> Dict[str, Any]:
        return self._request("POST", "/v3/assets", asset.to_management_payload())

    def register_policy(self, policy: EDCPolicy) -> Dict[str, Any]:
        return self._request("POST", "/v3/policydefinitions", policy.to_management_payload())

    def register_contract(self, contract: ContractDefinition) -> Dict[str, Any]:
        return self._request("POST", "/v3/contractdefinitions", contract.to_management_payload())


# ═════════════════════════════════════════════════════════════════════════════
# 실제 BaSyx AAS HTTP 클라이언트
# ═════════════════════════════════════════════════════════════════════════════

class BaSyxAASClient:
    """BaSyx AAS Server v3 REST API 클라이언트.

    CATENAX_AAS_BASE_URL 환경변수가 설정되면 자동 활성화됩니다.
    """

    def __init__(self, aas_base_url: str, auth_key: Optional[str] = None,
                 timeout: float = 15.0):
        self.aas_base_url = aas_base_url.rstrip("/")
        self.auth_key     = auth_key
        self.timeout      = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_key:
            h["X-Api-Key"] = self.auth_key
        return h

    def _req(self, method: str, url: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        import urllib.parse as _up
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        req  = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} {url}: {exc.read().decode()}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"연결 실패 {url}: {exc.reason}") from exc

    def upsert_shell(self, shell: AASShell) -> Dict[str, Any]:
        url = f"{self.aas_base_url}/shells"
        try:
            return self._req("POST", url, shell.to_dict())
        except RuntimeError as exc:
            if "409" in str(exc):
                import urllib.parse
                return self._req("PUT", f"{url}/{urllib.parse.quote(shell.shell_id, safe='')}", shell.to_dict())
            raise

    def upsert_submodel(self, submodel: AASSubmodel) -> Dict[str, Any]:
        import urllib.parse
        enc = urllib.parse.quote(submodel.submodel_id, safe="")
        try:
            return self._req("PUT", f"{self.aas_base_url}/submodels/{enc}", submodel.to_dict())
        except RuntimeError as exc:
            if "404" in str(exc):
                return self._req("POST", f"{self.aas_base_url}/submodels", submodel.to_dict())
            raise


# ═════════════════════════════════════════════════════════════════════════════
# CobotEDCPipeline — 핵심 오케스트레이터
# ═════════════════════════════════════════════════════════════════════════════

class CobotEDCPipeline:
    """협동로봇 EDC + AAS 통합 파이프라인 오케스트레이터.

    6단계 처리 흐름:
    ─────────────────────────────────────────────────────
    Step 1  _parse_raw()        raw dict → RawTelemetry
    Step 2  _preprocess()       → NormalizedTelemetry
    Step 3  _map_to_aas()       → AASShell + AASSubmodel
    Step 4  _register_edc()     → EDCAsset/Policy/Contract 등록
    Step 5  _ai_validate()      → Ollama 검증 (가능하면 자동)
    Step 6  _upsert_aas()       → AAS INSERT or UPDATE
    ─────────────────────────────────────────────────────

    Mock 모드:  edc_client=None, aas_client=None (기본값)
    Real 모드:  edc_client=EDCHttpClient(), aas_client=BaSyxAASClient()
    """

    def __init__(
        self,
        aas_store:  Optional[AASStore]       = None,
        edc_store:  Optional[EDCStore]       = None,
        edc_client: Optional[EDCHttpClient]  = None,  # None = Mock
        aas_client: Optional[BaSyxAASClient] = None,  # None = Mock
        thresholds: Optional[TelemetryThresholds] = None,
        ai_disabled: Optional[bool] = None,
    ):
        self._aas_store    = aas_store  or AASStore()
        self._edc_store    = edc_store  or EDCStore()
        self._edc_client   = edc_client
        self._aas_client   = aas_client
        self._preprocessor = TelemetryPreprocessor(thresholds)
        self._mapper       = AASMapper()
        # None → 환경 변수 CATENAX_DISABLE_AI / True·False → 명시적 오버라이드
        if ai_disabled is None:
            self._ai_disabled = _ai_disabled_by_env()
        else:
            self._ai_disabled = bool(ai_disabled)

    # ─────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────────────────

    def onboard(
        self,
        raw_dict:           Dict[str, Any],
        provider_bpn:       str,
        cobot_api_base_url: str = "http://localhost:8080",
        cobot_data_path:    str = "/api/v1/cobot/telemetry",
        policy_type:        str = "bpn",
        edc_asset_id:       Optional[str] = None,
    ) -> Dict[str, Any]:
        """전체 파이프라인 실행: raw telemetry → AAS 저장 + EDC 등록.

        sample_telemetry.json 하나로 모든 단계를 실행합니다.

        Returns:
            파이프라인 실행 결과 요약 (JSON 직렬화 가능)
        """
        result: Dict[str, Any] = {"pipeline": "onboard", "started_at": _utc_now()}

        # Step 1: raw data mapping ─────────────────────────────────────────────
        LOGGER.info("── Step 1: raw data mapping")
        raw = self._parse_raw(raw_dict)
        result["robot_id"] = raw.robot_id

        # Step 2: 전처리 ──────────────────────────────────────────────────────
        LOGGER.info("── Step 2: 전처리 (preprocess)")
        normalized = self._preprocess(raw)
        result["quality_flag"] = normalized.quality_flag.value
        result["yield_rate"]   = normalized.yield_rate
        result["issue_count"]  = len(normalized.issues)

        # Step 3: AAS 매핑 ────────────────────────────────────────────────────
        LOGGER.info("── Step 3: AAS 매핑")
        shell, submodel = self._map_to_aas(normalized)
        result["aas_shell_id"]    = shell.shell_id
        result["aas_submodel_id"] = submodel.submodel_id

        # Step 4: EDC 에셋/정책/컨트랙트 등록 ────────────────────────────────
        LOGGER.info("── Step 4: EDC 에셋/정책/컨트랙트 등록")
        if edc_asset_id and str(edc_asset_id).strip():
            asset_id = str(edc_asset_id).strip()
        else:
            asset_id = _make_asset_id(raw.robot_id)
        result["edc"] = self._register_edc(
            normalized   = normalized,
            asset_id     = asset_id,
            provider_bpn = provider_bpn,
            base_url     = cobot_api_base_url,
            data_path    = cobot_data_path,
            policy_type  = policy_type,
            submodel_id  = submodel.submodel_id,
        )

        # Step 5: AI 검증 (전처리·등록 완료 후, Ollama 가능 시 자동) ───────────
        # AI는 결정권이 없는 advisory 역할 — 실패해도 파이프라인은 계속됩니다.
        LOGGER.info("── Step 5: AI 검증 (Ollama)")
        result["ai_validation"] = self._ai_validate(normalized)

        # Step 6: AAS upsert (INSERT or UPDATE) ────────────────────────────────
        LOGGER.info("── Step 6: AAS upsert")
        result["aas"] = self._upsert_aas(shell, submodel)

        result["completed_at"] = _utc_now()
        result["success"]      = True
        LOGGER.info("✔ 온보딩 완료: robot_id=%s asset_id=%s", raw.robot_id, asset_id)
        return result

    def sync_aas(self, raw_dict: Dict[str, Any]) -> Dict[str, Any]:
        """AAS만 재동기화 (EDC 등록 없음).

        주기적인 텔레메트리 업데이트에 사용합니다.
        onboard 이후 데이터를 갱신할 때 이 커맨드를 사용하세요.

        흐름: Step1(raw mapping) → Step2(전처리) → Step3(AAS 매핑)
              → Step5(AI 검증, Ollama) → Step6(AAS upsert)
        """
        LOGGER.info("── sync-aas 시작")
        # Step 1
        raw        = self._parse_raw(raw_dict)
        # Step 2
        normalized = self._preprocess(raw)
        # Step 3
        shell, submodel = self._map_to_aas(normalized)
        # Step 5 (Ollama, 가능 시)
        ai_result  = self._ai_validate(normalized)
        # Step 6
        aas_result = self._upsert_aas(shell, submodel)
        LOGGER.info("✔ AAS 동기화 완료: robot_id=%s quality=%s",
                    raw.robot_id, normalized.quality_flag.value)
        return {
            "pipeline":     "sync-aas",
            "robot_id":     raw.robot_id,
            "quality_flag": normalized.quality_flag.value,
            "yield_rate":   normalized.yield_rate,
            "issue_count":  len(normalized.issues),
            "ai_validation": ai_result,
            "aas":          aas_result,
            "completed_at": _utc_now(),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: raw data mapping
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_raw(self, raw_dict: Dict[str, Any]) -> RawTelemetry:
        """딕셔너리 → RawTelemetry. 필수 필드 누락 시 InvalidTelemetryError."""
        try:
            return RawTelemetry.from_dict(raw_dict)
        except KeyError as exc:
            raise InvalidTelemetryError(f"필수 필드 누락: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise InvalidTelemetryError(f"필드 타입 오류: {exc}") from exc

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: 전처리
    # ─────────────────────────────────────────────────────────────────────────

    def _preprocess(self, raw: RawTelemetry) -> NormalizedTelemetry:
        """임계값 검사, 품질 플래그 부여, 수율 계산."""
        normalized = self._preprocessor.process(raw)
        for issue in normalized.issues:
            lvl = logging.WARNING if issue.severity == QualityFlag.WARNING else logging.ERROR
            LOGGER.log(lvl, "  이슈 [%s] %s: %s", issue.severity.value, issue.field, issue.message)
        return normalized

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: AAS 매핑
    # ─────────────────────────────────────────────────────────────────────────

    def _map_to_aas(self, normalized: NormalizedTelemetry) -> Tuple[AASShell, AASSubmodel]:
        """NormalizedTelemetry → (AASShell, AASSubmodel)."""
        return self._mapper.build_shell_and_submodel(normalized)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: EDC 에셋/정책/컨트랙트 등록
    # ─────────────────────────────────────────────────────────────────────────

    def _register_edc(
        self,
        normalized:   NormalizedTelemetry,
        asset_id:     str,
        provider_bpn: str,
        base_url:     str,
        data_path:    str,
        policy_type:  str,
        submodel_id:  str,
    ) -> Dict[str, Any]:
        """에셋 + 접근정책 + 계약정책 + 컨트랙트정의 + 카탈로그 등록.

        정책 설계:
            access_policy   = 에셋을 볼 수 있는 주체 정의
            contract_policy = 계약 성립 조건 정의
            → 두 정책이 모두 통과되어야 계약 성립

        실제 EDC 연동 시:
            self._edc_client가 주입되면 HTTP API를 호출합니다.
            없으면 로컬 Mock 저장소(EDCStore)에 저장합니다.
        """
        # 에셋 생성
        asset = EDCAsset(
            asset_id     = asset_id,
            name         = f"Cobot 텔레메트리 — {normalized.robot_id}",
            description  = (
                f"협동로봇 {normalized.robot_id} 운용 데이터. "
                f"라인: {normalized.line_id}, 스테이션: {normalized.station_id}"
            ),
            base_url     = base_url,
            data_path    = data_path,
            provider_bpn = provider_bpn,
            extra        = {
                "catenax:robotId":   normalized.robot_id,
                "catenax:lineId":    normalized.line_id,
                "catenax:stationId": normalized.station_id,
                "aas:submodelId":    submodel_id,
            },
        )

        # 정책 생성 (접근정책 + 계약정책)
        access_policy   = _make_policy(f"{asset_id}-access",   asset_id, policy_type, provider_bpn)
        contract_policy = _make_policy(f"{asset_id}-contract", asset_id, policy_type, provider_bpn)

        # 컨트랙트 정의 (에셋 + 두 정책 연결)
        contract = ContractDefinition(
            contract_definition_id = f"{asset_id}-contractdef",
            access_policy_id       = access_policy.policy_id,
            contract_policy_id     = contract_policy.policy_id,
            asset_id               = asset_id,
        )

        # 저장 (Mock 또는 Real)
        if self._edc_client:
            asset_r    = self._edc_client.register_asset(asset)
            access_r   = self._edc_client.register_policy(access_policy)
            contract_r = self._edc_client.register_policy(contract_policy)
            cdef_r     = self._edc_client.register_contract(contract)
            mode       = "http_api"
        else:
            asset_r    = self._edc_store.register_asset(asset)
            access_r   = self._edc_store.register_policy(access_policy)
            contract_r = self._edc_store.register_policy(contract_policy)
            cdef_r     = self._edc_store.register_contract(contract)
            mode       = "local_mock"

        # 카탈로그 갱신 (policy 정보 포함)
        self._edc_store.upsert_catalog_entry(CatalogEntry(
            asset_id           = asset_id,
            asset_name         = asset.name,
            provider_bpn       = provider_bpn,
            contract_id        = contract.contract_definition_id,
            semantic_id        = asset.semantic_id,
            description        = asset.description,
            robot_id           = normalized.robot_id,
            policy_type        = policy_type,
            access_policy_id   = access_policy.policy_id,
            contract_policy_id = contract_policy.policy_id,
        ))

        return {
            "asset_id":       asset_id,
            "policy_type":    policy_type,
            "asset":          asset_r,
            "access_policy":  access_r,
            "contract_policy": contract_r,
            "contract_def":   cdef_r,
            "mode":           mode,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Step 5: AI 검증 (Ollama, 자동 시도)
    # ─────────────────────────────────────────────────────────────────────────

    def _ai_validate(self, normalized: NormalizedTelemetry) -> Dict[str, Any]:
        """Ollama AI로 텔레메트리 이상 징후를 분석합니다.

        기본적으로 호출을 시도합니다. ``--no-ai`` 또는 ``CATENAX_DISABLE_AI`` 로 끌 수 있습니다.
        Ollama를 사용할 수 없어도 예외를 외부로 전파하지 않습니다.
        """
        if self._ai_disabled:
            return {"ok": False, "reason": "AI 비활성화 (--no-ai 또는 CATENAX_DISABLE_AI)"}
        if not _AI_AVAILABLE:
            return {"ok": False, "reason": "ai_helpers 모듈 없음"}

        try:
            if not check_ollama_available():
                return {"ok": False, "reason": "Ollama 서버 미응답 — $ ollama serve 로 시작하세요"}
            return validate_with_ai(normalized.to_dict())
        except OllamaUnavailableError as exc:
            LOGGER.warning("Ollama 사용 불가: %s", exc)
            return {"ok": False, "reason": str(exc)}

    # ─────────────────────────────────────────────────────────────────────────
    # Step 6: AAS upsert
    # ─────────────────────────────────────────────────────────────────────────

    def _upsert_aas(self, shell: AASShell, submodel: AASSubmodel) -> Dict[str, Any]:
        """Shell + Submodel을 저장소에 upsert (INSERT or UPDATE).

        기존 데이터 유무를 확인하여 자동으로 INSERT/UPDATE를 결정합니다.
        실제 BaSyx 연동 시 aas_client를 주입합니다.
        """
        if self._aas_client:
            shell_r    = self._aas_client.upsert_shell(shell)
            submodel_r = self._aas_client.upsert_submodel(submodel)
            mode       = "basyx_api"
        else:
            shell_r    = self._aas_store.upsert_shell(shell)
            submodel_r = self._aas_store.upsert_submodel(submodel)
            mode       = "local_mock"

        return {"shell": shell_r, "submodel": submodel_r, "mode": mode}


# ═════════════════════════════════════════════════════════════════════════════
# 환경변수 기반 파이프라인 팩토리
# ═════════════════════════════════════════════════════════════════════════════

def build_pipeline_from_env(ai_disabled: Optional[bool] = None) -> CobotEDCPipeline:
    """환경변수 설정에 따라 Mock 또는 실제 HTTP 클라이언트를 주입합니다.

    ``ai_disabled`` 가 None이면 ``CATENAX_DISABLE_AI`` 환경 변수를 따릅니다.
    프로그램에서 강제로 끄려면 ``build_pipeline_from_env(ai_disabled=True)``.

    ── 필수 환경변수 (실제 EDC 연동 시) ───────────────────────────────────────
    CATENAX_EDC_MANAGEMENT_URL   http://edc-provider:8080/management

    ── 선택 환경변수 ───────────────────────────────────────────────────────────
    CATENAX_EDC_API_KEY          EDC Management API 키
    CATENAX_AAS_BASE_URL         http://basyx:8081  (실제 BaSyx 연동 시)
    CATENAX_AAS_API_KEY          BaSyx API 키
    CATENAX_STORE_DIR            로컬 저장소 경로 (기본: ./store)
    CATENAX_MOCK_DATA_DIR        위와 동일 (이전 이름 호환)
    CATENAX_DISABLE_AI           1/true/yes 이면 Ollama 단계 생략
    OLLAMA_BASE_URL              http://localhost:11434
    OLLAMA_MODEL                 llama3
    """
    edc_mgmt_url = os.environ.get("CATENAX_EDC_MANAGEMENT_URL")
    edc_api_key  = os.environ.get("CATENAX_EDC_API_KEY")
    aas_base_url = os.environ.get("CATENAX_AAS_BASE_URL")
    aas_api_key  = os.environ.get("CATENAX_AAS_API_KEY")

    edc_client: Optional[EDCHttpClient]  = None
    aas_client: Optional[BaSyxAASClient] = None

    if edc_mgmt_url:
        edc_client = EDCHttpClient(management_url=edc_mgmt_url, api_key=edc_api_key)
        LOGGER.info("실제 EDC 연동 활성화: %s", edc_mgmt_url)
    else:
        LOGGER.info("EDC Mock 모드 (CATENAX_EDC_MANAGEMENT_URL 미설정 → 로컬 저장소 사용)")

    if aas_base_url:
        aas_client = BaSyxAASClient(aas_base_url=aas_base_url, auth_key=aas_api_key)
        LOGGER.info("실제 BaSyx AAS 연동 활성화: %s", aas_base_url)
    else:
        LOGGER.info("AAS Mock 모드 (CATENAX_AAS_BASE_URL 미설정 → 로컬 저장소 사용)")

    return CobotEDCPipeline(edc_client=edc_client, aas_client=aas_client, ai_disabled=ai_disabled)


# ═════════════════════════════════════════════════════════════════════════════
# CLI 커맨드 핸들러
# ═════════════════════════════════════════════════════════════════════════════

def _cmd_onboard(args: argparse.Namespace, pipeline: CobotEDCPipeline) -> None:
    records = _load_telemetry_records(Path(args.telemetry_json))
    if not getattr(args, "all_records", False):
        records = records[:1]
    results: List[Dict[str, Any]] = []
    for raw in records:
        results.append(
            pipeline.onboard(
                raw_dict           = raw,
                provider_bpn       = args.provider_bpn,
                cobot_api_base_url = args.cobot_api_base_url,
                cobot_data_path    = args.cobot_data_path,
                policy_type        = args.policy_type,
                edc_asset_id       = getattr(args, "asset_id", None),
            )
        )
    if len(results) == 1:
        print(json.dumps(results[0], indent=2, ensure_ascii=False))
    else:
        print(
            json.dumps(
                {"pipeline": "onboard-batch", "count": len(results), "results": results},
                indent=2,
                ensure_ascii=False,
            )
        )


def _cmd_sync_aas(args: argparse.Namespace, pipeline: CobotEDCPipeline) -> None:
    records = _load_telemetry_records(Path(args.telemetry_json))
    if not getattr(args, "all_records", False):
        records = records[:1]
    results: List[Dict[str, Any]] = [pipeline.sync_aas(raw) for raw in records]
    if len(results) == 1:
        print(json.dumps(results[0], indent=2, ensure_ascii=False))
    else:
        print(
            json.dumps(
                {"pipeline": "sync-aas-batch", "count": len(results), "results": results},
                indent=2,
                ensure_ascii=False,
            )
        )


def _cmd_export_catalog(_args: argparse.Namespace, pipeline: CobotEDCPipeline) -> None:
    catalog = pipeline._edc_store.list_catalog()
    if not catalog:
        print('카탈로그가 비어 있습니다. 먼저 "onboard" 커맨드를 실행하세요.')
        return
    print(json.dumps({
        "catalog_count": len(catalog),
        "entries":       catalog,
        "exported_at":   _utc_now(),
    }, indent=2, ensure_ascii=False))


def _cmd_list(_args: argparse.Namespace, pipeline: CobotEDCPipeline) -> None:
    assets    = pipeline._edc_store.list_assets()
    shells    = pipeline._aas_store.list_shells()
    submodels = pipeline._aas_store.list_submodels()
    print(json.dumps({
        "edc_assets": [
            {"asset_id": a["asset_id"], "name": a["name"], "registered_at": a["registered_at"]}
            for a in assets
        ],
        "aas_shells": [
            {"id": s["id"], "idShort": s["idShort"],
             "updated_at": s.get("_meta", {}).get("updated_at")}
            for s in shells
        ],
        "aas_submodels": [
            {"id": s["id"], "idShort": s["idShort"],
             "updated_at": s.get("_meta", {}).get("updated_at")}
            for s in submodels
        ],
        "summary": {
            "asset_count":    len(assets),
            "shell_count":    len(shells),
            "submodel_count": len(submodels),
        },
    }, indent=2, ensure_ascii=False))


# ═════════════════════════════════════════════════════════════════════════════
# CLI 진입점
# ═════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[Iterable[str]] = None) -> int:
    """CLI 진입점.

    ┌─────────────────────────────────────────────────────────────────────┐
    │ 커맨드          설명                                                │
    ├────────────────┬────────────────────────────────────────────────────┤
    │ onboard        │ 전체 파이프라인: raw→전처리→AAS→EDC 등록          │
    │ sync-aas       │ AAS만 재동기화 (EDC 등록 생략)                    │
    │ export-catalog │ 로컬 카탈로그 JSON 출력                           │
    │ list           │ 등록된 에셋·AAS 목록 출력                         │
    └────────────────┴────────────────────────────────────────────────────┘

    빠른 시작 (sample_telemetry.json 기준):

        # 1. 전체 온보딩
        python3 edc.py onboard \\
            --telemetry-json sample_telemetry.json \\
            --provider-bpn BPNL000000000001

        # 2. AI 끄고 온보딩 (기본은 Ollama 자동 시도)
        python3 edc.py onboard \\
            --telemetry-json sample_telemetry.json \\
            --provider-bpn BPNL000000000001 --no-ai

        # 3. AAS만 업데이트
        python3 edc.py sync-aas --telemetry-json sample_telemetry.json

        # 4. 카탈로그 확인
        python3 edc.py export-catalog

        # 5. 등록 목록 확인
        python3 edc.py list
    """
    parser = argparse.ArgumentParser(
        prog        = "edc.py",
        description = "Catena-X 협동로봇 데이터 플랫폼 CLI",
        formatter_class = argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    sub = parser.add_subparsers(dest="command", required=True)

    # ── onboard ───────────────────────────────────────────────────────────────
    p_on = sub.add_parser("onboard", help="전체 파이프라인 실행 (raw → AAS + EDC)")
    p_on.add_argument("--telemetry-json",     required=True)
    p_on.add_argument("--provider-bpn",       required=True)
    p_on.add_argument("--cobot-api-base-url", default="http://localhost:8080")
    p_on.add_argument("--cobot-data-path",    default="/api/v1/cobot/telemetry")
    p_on.add_argument(
        "--asset-id",
        default=None,
        help="EDC 에셋 ID (미지정 시 urn:catenax:cobot:<robot_id>:telemetry 규칙)",
    )
    p_on.add_argument("--policy-type",
                      choices=["bpn", "membership", "open"], default="bpn")
    p_on.add_argument(
        "--no-ai",
        action="store_true",
        help="Ollama AI 검증 생략 (기본: 가능하면 자동 호출)",
    )
    p_on.add_argument(
        "--all-records",
        action="store_true",
        help="JSON이 배열일 때 모든 레코드에 대해 온보딩 (기본: 첫 레코드만)",
    )

    # ── sync-aas ──────────────────────────────────────────────────────────────
    p_sync = sub.add_parser("sync-aas", help="AAS만 재동기화")
    p_sync.add_argument("--telemetry-json", required=True)
    p_sync.add_argument(
        "--no-ai",
        action="store_true",
        help="Ollama AI 검증 생략 (기본: 가능하면 자동 호출)",
    )
    p_sync.add_argument(
        "--all-records",
        action="store_true",
        help="JSON이 배열일 때 모든 레코드에 대해 동기화 (기본: 첫 레코드만)",
    )

    # ── export-catalog ────────────────────────────────────────────────────────
    sub.add_parser("export-catalog", help="로컬 카탈로그 출력")

    # ── list ──────────────────────────────────────────────────────────────────
    sub.add_parser("list", help="등록된 에셋·AAS 목록 출력")

    args     = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level  = getattr(logging, args.log_level),
        format = "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    no_ai = bool(getattr(args, "no_ai", False))
    pipeline = build_pipeline_from_env(ai_disabled=True if no_ai else None)

    try:
        if args.command == "onboard":
            _cmd_onboard(args, pipeline)
        elif args.command == "sync-aas":
            _cmd_sync_aas(args, pipeline)
        elif args.command == "export-catalog":
            _cmd_export_catalog(args, pipeline)
        elif args.command == "list":
            _cmd_list(args, pipeline)

    except InvalidTelemetryError as exc:
        LOGGER.error("텔레메트리 오류: %s", exc)
        return 1
    except FileNotFoundError as exc:
        LOGGER.error("파일 없음: %s", exc)
        return 1
    except (EDCStoreError, AASStoreError) as exc:
        LOGGER.error("저장소 오류: %s", exc)
        return 1
    except KeyboardInterrupt:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
