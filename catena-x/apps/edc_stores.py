"""edc_stores.py — “결과를 어디에 남길지”만 정리하는 용.

Objective:  ``edc.py`` 가 telemetry (로봇 JSON)을 받아
전처리 → AAS(자산 정보) → EDC(데이터 상품·규칙) 순으로 **일을 정리**하고,
그 마지막 단계에서 “지금 만든 AAS/EDC 내용을 **어디에 쓸지**”가 여기서 결정됩니다.

  • **평소(설정 없음)** — ``catena-x/store/`` 아래에 JSON 파일로 저장합니다.
    로봇·라인 정보는 ``store/aas/``, 데이터 카탈로그·정책 등은 ``store/edc/`` 쪽입니다.
    이게 데모용 **로컬 목업**이에요. 경로는 ``CATENAX_STORE_DIR`` 등으로 바꿀 수 있습니다.

  • **나중에 실서버를 쓸 때** — 같은 자리에서 ``EDCHttpClient`` / ``BaSyxAASClient`` 가
    **진짜 EDC·AAS 서버 주소**로 HTTP 요청을 보냅니다. (환경 변수로 URL만 주면 됩니다.)

``edc.py`` 는 “언제 무엇을 할지”를 짜는 감독, 이 파일은 “저장/전송 창구”
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from models import (
    AASShell,
    AASSubmodel,
    CatalogEntry,
    ContractDefinition,
    EDCAsset,
    EDCPolicy,
    EDCStoreError,
)

_APPS_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("catenax.edc")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _store_root() -> Path:
    # GitHub 기본: CATENAX_STORE_DIR — 기존 로컬 실험과 호환: CATENAX_MOCK_DATA_DIR 도 허용
    # 환경 변수 없을 때: catena-x/store (`edc_stores.py`는 catena-x/apps/ 아래에 있음)
    base = os.environ.get("CATENAX_STORE_DIR") or os.environ.get("CATENAX_MOCK_DATA_DIR")
    default_store = _APPS_DIR.parent / "store"
    d = Path(base or str(default_store))
    (d / "aas").mkdir(parents=True, exist_ok=True)
    (d / "edc").mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(value: str) -> str:
    return re.sub(r"[^\w\-]", "_", value)


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
        path = self._root / f"{_safe_filename(shell.shell_id)}_shell.json"
        is_update = path.exists()

        if is_update:
            existing = json.loads(path.read_text(encoding="utf-8"))
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
        path = self._root / f"{_safe_filename(submodel.submodel_id)}_submodel.json"
        is_update = path.exists()

        if is_update:
            existing = json.loads(path.read_text(encoding="utf-8"))
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
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self._root.glob("*_shell.json"))]

    def list_submodels(self) -> List[Dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self._root.glob("*_submodel.json"))]


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

    def register_asset(self, asset: EDCAsset) -> Dict[str, Any]:
        """에셋 등록. 실제 EDC 연동 시 → POST /v3/assets"""
        store = self._load("assets.json")
        store[asset.asset_id] = asset.to_dict()
        self._save("assets.json", store)
        LOGGER.info("EDC 에셋 등록: %s", asset.asset_id)
        return {"registered": True, "asset_id": asset.asset_id}

    def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        return self._load("assets.json").get(asset_id)

    def list_assets(self) -> List[Dict[str, Any]]:
        return list(self._load("assets.json").values())

    def register_policy(self, policy: EDCPolicy) -> Dict[str, Any]:
        """정책 등록. 실제 EDC 연동 시 → POST /v3/policydefinitions"""
        store = self._load("policies.json")
        store[policy.policy_id] = policy.to_dict()
        self._save("policies.json", store)
        LOGGER.info("EDC 정책 등록: %s [%s]", policy.policy_id, policy.policy_type.value)
        return {"registered": True, "policy_id": policy.policy_id}

    def list_policies(self) -> List[Dict[str, Any]]:
        return list(self._load("policies.json").values())

    def register_contract(self, contract: ContractDefinition) -> Dict[str, Any]:
        """컨트랙트 등록. 실제 EDC 연동 시 → POST /v3/contractdefinitions"""
        store = self._load("contracts.json")
        store[contract.contract_definition_id] = contract.to_dict()
        self._save("contracts.json", store)
        LOGGER.info("EDC 컨트랙트 등록: %s", contract.contract_definition_id)
        return {"registered": True, "contract_id": contract.contract_definition_id}

    def list_contracts(self) -> List[Dict[str, Any]]:
        return list(self._load("contracts.json").values())

    def upsert_catalog_entry(self, entry: CatalogEntry) -> Dict[str, Any]:
        store = self._load("catalog.json")
        store[entry.asset_id] = entry.to_dict()
        self._save("catalog.json", store)
        LOGGER.info("카탈로그 갱신: %s", entry.asset_id)
        return {"updated": True, "asset_id": entry.asset_id}

    def list_catalog(self) -> List[Dict[str, Any]]:
        return list(self._load("catalog.json").values())

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

    def __init__(
        self,
        management_url: str,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ):
        self.management_url = management_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.management_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8").strip()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in self.RETRYABLE_CODES and attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
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

    def __init__(self, aas_base_url: str, auth_key: Optional[str] = None, timeout: float = 15.0):
        self.aas_base_url = aas_base_url.rstrip("/")
        self.auth_key = auth_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_key:
            h["X-Api-Key"] = self.auth_key
        return h

    def _req(self, method: str, url: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
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
                return self._req(
                    "PUT",
                    f"{url}/{urllib.parse.quote(shell.shell_id, safe='')}",
                    shell.to_dict(),
                )
            raise

    def upsert_submodel(self, submodel: AASSubmodel) -> Dict[str, Any]:
        enc = urllib.parse.quote(submodel.submodel_id, safe="")
        try:
            return self._req("PUT", f"{self.aas_base_url}/submodels/{enc}", submodel.to_dict())
        except RuntimeError as exc:
            if "404" in str(exc):
                return self._req("POST", f"{self.aas_base_url}/submodels", submodel.to_dict())
            raise
