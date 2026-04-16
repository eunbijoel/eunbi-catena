"""Catena-X EDC 커넥터 – 공장 협동로봇(Cobot) 데이터 AAS 연동 구현체.

BerePi 프로젝트(github.com/jeonghoonkang/BerePi) 의 apps/catenax 코드를 기반으로
아래 항목을 확장·보완한 파일입니다.

확장 내용
─────────
1. HttpJsonClient       : 재시도(Retry) + 지수 백오프 정책 강화
2. EDCAsset             : Catena-X v0.0.1/ns 컨텍스트, AAS semanticId 지원
3. EDCPolicy            : ODRL 팩토리 메서드 (BPN / 멤버십 / 개방 정책)
4. ContractDefinition   : 변경 없음 (원본 유지)
5. FactoryCobotTelemetry: 관절 토크·속도·힘-토크 센서, 안전 상태, 진단 필드 추가
6. AASBridge            : AAS Part 2(IDTA-01002-3-0) REST v3 준수
                          · Shell / Submodel 자동 생성·갱신 (upsert)
                          · SubmodelElementCollection 구조적 매핑
                          · 레지스트리(shell-descriptors) 등록 지원
7. EDCConnectorService  : 카탈로그 조회, 협상 상태 폴링, 데이터 전송 시작
8. CobotEDCPipeline     : 온보딩 + AAS 동기화 + 협상-전송 워크플로
9. build_pipeline_from_env / main : 원본 CLI 구조 유지, 커맨드 추가

표준 참조
─────────
- Eclipse EDC (Tractus-X) Management API v3
- IDS / DSP (Dataspace Protocol) v2024-1
- IDTA AAS Part 2 (IDTA-01002-3-0) REST
- IDTA Submodel Template 협동로봇 (IDTA-02017)
- Catena-X ODRL Profile (catenax-eV/cx-odrl-profile)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# 로거
# ──────────────────────────────────────────────────────────────────────────────
LOGGER = logging.getLogger("catenax.edc")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# HTTP 클라이언트 (표준 라이브러리, 재시도 포함)
# ──────────────────────────────────────────────────────────────────────────────

class HttpJsonClient:
    """표준 라이브러리 기반 JSON-over-HTTP 클라이언트.

    5xx / 연결 오류 시 지수 백오프(exponential back-off) 재시도를 수행합니다.
    라즈베리파이 등 경량 환경에서도 의존성 없이 동작합니다.
    """

    RETRYABLE_CODES = {429, 500, 502, 503, 504}

    def __init__(self, timeout: float = 15.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries

    def request_json(
        self,
        method: str,
        url: str,
        payload: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        body: Optional[bytes] = None
        final_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if headers:
            final_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        attempt = 0
        last_exc: Exception = RuntimeError("No attempts made")
        while attempt <= self.max_retries:
            try:
                req = urllib.request.Request(url, data=body, headers=final_headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8").strip()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in self.RETRYABLE_CODES and attempt < self.max_retries:
                    wait = 2 ** attempt
                    LOGGER.warning("HTTP %s %s → %ds 후 재시도 (%d/%d)", exc.code, url, wait, attempt + 1, self.max_retries)
                    time.sleep(wait)
                    attempt += 1
                    last_exc = RuntimeError(f"HTTP {exc.code}: {detail}")
                    continue
                raise RuntimeError(f"HTTP {exc.code} calling {url}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    LOGGER.warning("연결 실패 %s → %ds 후 재시도 (%d/%d)", url, wait, attempt + 1, self.max_retries)
                    time.sleep(wait)
                    attempt += 1
                    last_exc = RuntimeError(f"URLError: {exc.reason}")
                    continue
                raise RuntimeError(f"Failed to reach {url}: {exc.reason}") from exc
        raise last_exc


# ──────────────────────────────────────────────────────────────────────────────
# EDC 데이터 모델
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class EDCAsset:
    """EDC 에셋 정의.

    Catena-X 네임스페이스(w3id.org/edc/v0.0.1/ns)와
    AAS semanticId(IDTA-02017)를 포함합니다.
    """

    asset_id: str
    name: str
    base_url: str
    data_path: str
    description: str
    content_type: str = "application/json"
    provider_bpn: str = ""
    asset_type: str = "factory-cobot-telemetry"
    semantic_id: str = ""          # AAS Submodel SemanticId
    extra_properties: Dict[str, Any] = field(default_factory=dict)

    def to_management_payload(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {
            "asset:prop:id": self.asset_id,
            "asset:prop:name": self.name,
            "asset:prop:contenttype": self.content_type,
            "asset:prop:description": self.description,
            "catenax:assetType": self.asset_type,
        }
        if self.provider_bpn:
            props["catenax:providerBpn"] = self.provider_bpn
        if self.semantic_id:
            props["catenax:semanticId"] = self.semantic_id
            props["aas:semanticId"] = self.semantic_id
        props.update(self.extra_properties)

        return {
            "@context": {
                "@vocab": "https://w3id.org/edc/v0.0.1/ns/",
                "cx-common": "https://w3id.org/catenax/ontology/common#",
                "aas": "https://admin-shell.io/aas/3/0/",
            },
            "@id": self.asset_id,
            "properties": props,
            "dataAddress": {
                "@type": "HttpData",
                "type": "HttpData",
                "baseUrl": self.base_url.rstrip("/"),
                "path": self.data_path,
                "proxyMethod": "true",
                "proxyPath": "true",
                "proxyQueryParams": "true",
                "proxyBody": "true",
            },
        }


@dataclass(slots=True)
class EDCPolicy:
    """ODRL 정책 정의 (Catena-X ODRL Profile 준수).

    팩토리 메서드로 자주 쓰이는 정책 유형을 쉽게 생성할 수 있습니다.
    """

    policy_id: str
    target: str
    permissions: List[Dict[str, Any]] = field(default_factory=list)

    # ── 팩토리 메서드 ─────────────────────────────────────────────────────────

    @classmethod
    def bpn_restricted(cls, policy_id: str, target: str, allowed_bpn: str) -> "EDCPolicy":
        """특정 BPN만 허용하는 정책."""
        return cls(
            policy_id=policy_id,
            target=target,
            permissions=[{
                "action": "USE",
                "constraint": {
                    "leftOperand": "BusinessPartnerNumber",
                    "operator": "EQ",
                    "rightOperand": allowed_bpn,
                },
                "target": target,
            }],
        )

    @classmethod
    def membership(cls, policy_id: str, target: str) -> "EDCPolicy":
        """Catena-X 멤버십 보유자 전체 허용 정책."""
        return cls(
            policy_id=policy_id,
            target=target,
            permissions=[{
                "action": "USE",
                "constraint": {
                    "leftOperand": "Membership",
                    "operator": "EQ",
                    "rightOperand": "active",
                },
                "target": target,
            }],
        )

    @classmethod
    def open_access(cls, policy_id: str, target: str) -> "EDCPolicy":
        """제약 없는 개방 정책 (내부 테스트용)."""
        return cls(
            policy_id=policy_id,
            target=target,
            permissions=[{"action": "USE", "target": target}],
        )

    # ── 직렬화 ────────────────────────────────────────────────────────────────

    def to_management_payload(self) -> Dict[str, Any]:
        return {
            "@context": {
                "@vocab": "https://w3id.org/edc/v0.0.1/ns/",
                "odrl": "http://www.w3.org/ns/odrl/2/",
            },
            "@id": self.policy_id,
            "@type": "PolicyDefinition",
            "policy": {
                "@context": "http://www.w3.org/ns/odrl.jsonld",
                "@type": "Set",
                "permission": self.permissions,
            },
        }


@dataclass(slots=True)
class ContractDefinition:
    """에셋 선택기와 정책을 묶는 컨트랙트 정의 (원본 BerePi 구조 유지)."""

    contract_definition_id: str
    access_policy_id: str
    contract_policy_id: str
    asset_id: str

    def to_management_payload(self) -> Dict[str, Any]:
        return {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@id": self.contract_definition_id,
            "@type": "ContractDefinition",
            "accessPolicyId": self.access_policy_id,
            "contractPolicyId": self.contract_policy_id,
            "assetsSelector": [
                {
                    "@type": "Criterion",
                    "operandLeft": "https://w3id.org/edc/v0.0.1/ns/id",
                    "operator": "=",
                    "operandRight": self.asset_id,
                }
            ],
        }


# ──────────────────────────────────────────────────────────────────────────────
# 협동로봇 텔레메트리 모델
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FactoryCobotTelemetry:
    """공장 협동로봇 텔레메트리 (IDTA-02017 Cobot Submodel Template 기반).

    필수 필드 (원본 BerePi)
    ────────────────────────
    robot_id, line_id, station_id, cycle_time_ms, power_watts,
    program_name, status

    선택 필드 (원본 BerePi)
    ────────────────────────
    good_parts, reject_parts, temperature_c, vibration_mm_s,
    pose, joint_positions_deg, alarms, produced_at

    확장 필드 (이번 구현)
    ─────────────────────
    joint_velocities_deg_s  : 관절 각속도 (deg/s)
    joint_torques_nm        : 관절 토크 (Nm)
    force_torque            : 엔드이펙터 힘·토크 (Fx Fy Fz Tx Ty Tz)
    safety_state            : 안전 상태 NORMAL / REDUCED / STOP
    payload_kg              : 현재 페이로드 (kg)
    speed_override_pct      : 속도 오버라이드 (0~100 %)
    total_operating_hours   : 누적 가동 시간 (h)
    uptime_ratio            : 가동률 0.0~1.0
    error_code              : 마지막 에러 코드
    diagnostics             : 진단 키-값 딕셔너리
    """

    # 필수
    robot_id: str
    line_id: str
    station_id: str
    cycle_time_ms: float
    power_watts: float
    program_name: str
    status: str                                         # RUNNING / IDLE / ERROR / STOP

    # 선택(원본)
    good_parts: int = 0
    reject_parts: int = 0
    temperature_c: Optional[float] = None
    vibration_mm_s: Optional[float] = None
    pose: Dict[str, float] = field(default_factory=dict)            # x y z rx ry rz (mm/deg)
    joint_positions_deg: Dict[str, float] = field(default_factory=dict)
    alarms: List[str] = field(default_factory=list)
    produced_at: str = field(default_factory=_utc_now)

    # 선택(확장)
    joint_velocities_deg_s: Dict[str, float] = field(default_factory=dict)
    joint_torques_nm: Dict[str, float] = field(default_factory=dict)
    force_torque: Dict[str, float] = field(default_factory=dict)    # Fx Fy Fz Tx Ty Tz
    safety_state: str = "NORMAL"
    payload_kg: float = 0.0
    speed_override_pct: float = 100.0
    total_operating_hours: float = 0.0
    uptime_ratio: float = 0.0
    error_code: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FactoryCobotTelemetry":
        def _float_opt(key: str) -> Optional[float]:
            v = raw.get(key)
            return float(v) if v is not None else None

        def _str_float_map(key: str) -> Dict[str, float]:
            return {str(k): float(v) for k, v in raw.get(key, {}).items()}

        return cls(
            robot_id=str(raw["robot_id"]),
            line_id=str(raw["line_id"]),
            station_id=str(raw["station_id"]),
            cycle_time_ms=float(raw["cycle_time_ms"]),
            power_watts=float(raw["power_watts"]),
            program_name=str(raw["program_name"]),
            status=str(raw["status"]),
            good_parts=int(raw.get("good_parts", 0)),
            reject_parts=int(raw.get("reject_parts", 0)),
            temperature_c=_float_opt("temperature_c"),
            vibration_mm_s=_float_opt("vibration_mm_s"),
            pose=_str_float_map("pose"),
            joint_positions_deg=_str_float_map("joint_positions_deg"),
            alarms=[str(a) for a in raw.get("alarms", [])],
            produced_at=str(raw.get("produced_at", _utc_now())),
            joint_velocities_deg_s=_str_float_map("joint_velocities_deg_s"),
            joint_torques_nm=_str_float_map("joint_torques_nm"),
            force_torque=_str_float_map("force_torque"),
            safety_state=str(raw.get("safety_state", "NORMAL")),
            payload_kg=float(raw.get("payload_kg", 0.0)),
            speed_override_pct=float(raw.get("speed_override_pct", 100.0)),
            total_operating_hours=float(raw.get("total_operating_hours", 0.0)),
            uptime_ratio=float(raw.get("uptime_ratio", 0.0)),
            error_code=str(raw.get("error_code", "")),
            diagnostics=dict(raw.get("diagnostics", {})),
        )


# ──────────────────────────────────────────────────────────────────────────────
# AAS 브릿지 (BaSyx AAS Server v3 REST API)
# ──────────────────────────────────────────────────────────────────────────────

class AASBridge:
    """협동로봇 텔레메트리를 AAS Submodel 형식으로 변환하고 서버에 업서트합니다.

    AAS Part 2(IDTA-01002-3-0) REST API v3 엔드포인트:
        POST /shells                  → Shell 생성
        PUT  /submodels/{id}          → Submodel 교체(upsert)
        POST /submodels               → Submodel 신규 생성
        POST /shell-descriptors       → 레지스트리 등록

    SubmodelElementCollection 구조 (IDTA-02017 기반):
        OperationalState      : 상태, 프로그램, 타임스탬프, 페이로드
        ProductionMetrics     : 부품 수량, 사이클 타임, 전력, 온도, 진동
        KinematicState        : 포즈, 관절 위치·속도·토크
        ForceTorqueSensor     : 엔드이펙터 힘·토크 (Fx Fy Fz Tx Ty Tz)
        SafetyAndDiagnostics  : 안전 상태, 알람, 에러 코드, 진단 데이터
    """

    # IDTA-02017 협동로봇 Submodel SemanticId
    COBOT_SEMANTIC_ID = "https://admin-shell.io/idta/CobotOperationalData/1/0"

    def __init__(
        self,
        aas_base_url: str,
        submodel_id: str,
        client: Optional[HttpJsonClient] = None,
        auth_key: Optional[str] = None,
        registry_url: Optional[str] = None,
        shell_id: Optional[str] = None,
    ):
        self.aas_base_url = aas_base_url.rstrip("/")
        self.submodel_id = submodel_id
        self.client = client or HttpJsonClient()
        self.auth_key = auth_key
        self.registry_url = (registry_url or "").rstrip("/")
        # shell_id 미지정 시 submodel_id에서 자동 생성
        self.shell_id = shell_id or submodel_id.replace("submodel", "shell")

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.auth_key:
            h["X-Api-Key"] = self.auth_key
        return h

    @staticmethod
    def _value_type(value: Any) -> str:
        if isinstance(value, bool):
            return "xs:boolean"
        if isinstance(value, int):
            return "xs:integer"
        if isinstance(value, float):
            return "xs:double"
        return "xs:string"

    @staticmethod
    def _prop(id_short: str, value: Any) -> Dict[str, Any]:
        """단순 Property SME 생성 헬퍼."""
        return {
            "modelType": "Property",
            "idShort": id_short,
            "valueType": AASBridge._value_type(value),
            "value": str(value) if not isinstance(value, str) else value,
        }

    @staticmethod
    def _collection(id_short: str, elements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """SubmodelElementCollection SME 생성 헬퍼."""
        return {
            "modelType": "SubmodelElementCollection",
            "idShort": id_short,
            "value": elements,
        }

    # ── 텔레메트리 → AAS Submodel 변환 ──────────────────────────────────────

    def telemetry_to_submodel(self, t: FactoryCobotTelemetry) -> Dict[str, Any]:
        """FactoryCobotTelemetry → AAS Submodel (IDTA-02017 구조) 변환."""
        elements: List[Dict[str, Any]] = [

            # OperationalState
            self._collection("OperationalState", [
                self._prop("RobotId", t.robot_id),
                self._prop("LineId", t.line_id),
                self._prop("StationId", t.station_id),
                self._prop("Status", t.status),
                self._prop("ProgramName", t.program_name),
                self._prop("ProducedAt", t.produced_at),
                self._prop("PayloadKg", t.payload_kg),
                self._prop("SpeedOverridePct", t.speed_override_pct),
                self._prop("TotalOperatingHours", t.total_operating_hours),
                self._prop("UptimeRatio", t.uptime_ratio),
            ]),

            # ProductionMetrics
            self._collection("ProductionMetrics", [
                self._prop("CycleTimeMs", t.cycle_time_ms),
                self._prop("PowerWatts", t.power_watts),
                self._prop("GoodParts", t.good_parts),
                self._prop("RejectParts", t.reject_parts),
                *([self._prop("TemperatureC", t.temperature_c)] if t.temperature_c is not None else []),
                *([self._prop("VibrationMmPerSec", t.vibration_mm_s)] if t.vibration_mm_s is not None else []),
            ]),

            # KinematicState : 포즈 + 관절 위치·속도·토크
            self._collection("KinematicState", [
                self._collection("EndEffectorPose", [
                    self._prop(axis.upper(), val) for axis, val in t.pose.items()
                ]),
                self._collection("JointPositionsDeg", [
                    self._prop(j, v) for j, v in t.joint_positions_deg.items()
                ]),
                self._collection("JointVelocitiesDegS", [
                    self._prop(j, v) for j, v in t.joint_velocities_deg_s.items()
                ]),
                self._collection("JointTorquesNm", [
                    self._prop(j, v) for j, v in t.joint_torques_nm.items()
                ]),
            ]),

            # ForceTorqueSensor : Fx Fy Fz Tx Ty Tz
            self._collection("ForceTorqueSensor", [
                self._prop(k, v) for k, v in t.force_torque.items()
            ]),

            # SafetyAndDiagnostics
            self._collection("SafetyAndDiagnostics", [
                self._prop("SafetyState", t.safety_state),
                self._prop("ErrorCode", t.error_code),
                self._prop("AlarmCount", len(t.alarms)),
                self._prop("Alarms", json.dumps(t.alarms, ensure_ascii=False)),
                *[self._prop(f"Diag_{k}", v) for k, v in t.diagnostics.items()],
            ]),
        ]

        return {
            "modelType": "Submodel",
            "id": self.submodel_id,
            "idShort": "CobotOperationalData",
            "semanticId": {
                "type": "ExternalReference",
                "keys": [{"type": "GlobalReference", "value": self.COBOT_SEMANTIC_ID}],
            },
            "submodelElements": elements,
        }

    # ── AAS Shell 생성 ────────────────────────────────────────────────────────

    def ensure_shell(self, robot_id: str, manufacturer: str = "") -> Dict[str, Any]:
        """Shell이 없으면 생성합니다 (이미 있으면 스킵)."""
        shell_payload: Dict[str, Any] = {
            "modelType": "AssetAdministrationShell",
            "id": self.shell_id,
            "idShort": f"Cobot_{robot_id.replace('-', '_')}",
            "assetInformation": {
                "assetKind": "Instance",
                "globalAssetId": f"urn:cobot:{robot_id}",
            },
            "description": [{"language": "ko", "text": f"협동로봇 {robot_id} AAS Shell"}],
            "submodels": [{"type": "ExternalReference", "keys": [
                {"type": "Submodel", "value": self.submodel_id}
            ]}],
        }
        if manufacturer:
            shell_payload["assetInformation"]["specificAssetIds"] = [
                {"name": "manufacturer", "value": manufacturer}
            ]

        url = f"{self.aas_base_url}/shells"
        try:
            result = self.client.request_json("POST", url, payload=shell_payload, headers=self._headers())
            LOGGER.info("AAS Shell 생성: %s", self.shell_id)
            return result
        except RuntimeError as exc:
            if "409" in str(exc) or "already" in str(exc).lower():
                LOGGER.debug("AAS Shell 이미 존재: %s", self.shell_id)
                return {"id": self.shell_id, "status": "already_exists"}
            raise

    # ── Submodel 업서트 ───────────────────────────────────────────────────────

    def upsert_telemetry(self, telemetry: FactoryCobotTelemetry) -> Dict[str, Any]:
        """Submodel을 생성하거나 전체 교체(PUT)합니다.

        BaSyx AAS Server v3 기준:
        - PUT /submodels/{encodedId} : 존재하면 교체, 없으면 404
        - POST /submodels            : 신규 생성
        """
        payload = self.telemetry_to_submodel(telemetry)
        headers = self._headers()
        encoded_id = urllib.parse.quote(self.submodel_id, safe="")
        put_url = f"{self.aas_base_url}/submodels/{encoded_id}"

        try:
            result = self.client.request_json("PUT", put_url, payload=payload, headers=headers)
            LOGGER.info("AAS Submodel PUT 완료: robot_id=%s", telemetry.robot_id)
            return result
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            LOGGER.info("Submodel 미존재 → POST로 신규 생성")

        post_url = f"{self.aas_base_url}/submodels"
        result = self.client.request_json("POST", post_url, payload=payload, headers=headers)
        LOGGER.info("AAS Submodel POST 완료: robot_id=%s", telemetry.robot_id)
        return result

    # ── AAS Registry 등록 ─────────────────────────────────────────────────────

    def register_shell_descriptor(
        self,
        robot_id: str,
        edc_data_plane_url: str,
        asset_id: str,
    ) -> Dict[str, Any]:
        """AAS Registry(shell-descriptors)에 Shell Descriptor를 등록합니다.

        소비자가 EDC를 통해 Submodel 데이터에 접근하는 DSP 엔드포인트를
        submodelDescriptors.endpoints 에 기록합니다.
        """
        if not self.registry_url:
            raise ValueError("registry_url이 설정되지 않았습니다 (CATENAX_AAS_REGISTRY_URL).")

        descriptor = {
            "id": self.shell_id,
            "idShort": f"Cobot_{robot_id.replace('-', '_')}",
            "assetKind": "Instance",
            "globalAssetId": f"urn:cobot:{robot_id}",
            "description": [{"language": "ko", "text": f"협동로봇 {robot_id}"}],
            "submodelDescriptors": [
                {
                    "id": self.submodel_id,
                    "idShort": "CobotOperationalData",
                    "semanticId": {
                        "type": "ExternalReference",
                        "keys": [{"type": "GlobalReference", "value": self.COBOT_SEMANTIC_ID}],
                    },
                    "endpoints": [
                        {
                            "interface": "SUBMODEL-3.0",
                            "protocolInformation": {
                                "href": (
                                    f"{edc_data_plane_url}/api/public/submodels/"
                                    f"{urllib.parse.quote(self.submodel_id, safe='')}"
                                ),
                                "endpointProtocol": "HTTP",
                                "endpointProtocolVersion": ["1.1"],
                                "subprotocol": "DSP",
                                "subprotocolBody": (
                                    f"id={asset_id};dspEndpoint={edc_data_plane_url}"
                                ),
                                "subprotocolBodyEncoding": "plain",
                            },
                        }
                    ],
                }
            ],
        }

        url = f"{self.registry_url}/shell-descriptors"
        try:
            result = self.client.request_json("POST", url, payload=descriptor, headers=self._headers())
            LOGGER.info("AAS Registry 등록 완료: %s", self.shell_id)
            return result
        except RuntimeError as exc:
            if "409" in str(exc):
                LOGGER.debug("Shell Descriptor 이미 존재 → PUT으로 갱신")
                put_url = f"{url}/{urllib.parse.quote(self.shell_id, safe='')}"
                return self.client.request_json("PUT", put_url, payload=descriptor, headers=self._headers())
            raise


# ──────────────────────────────────────────────────────────────────────────────
# EDC Management API 클라이언트
# ──────────────────────────────────────────────────────────────────────────────

class EDCConnectorService:
    """EDC Management API v3 래퍼.

    에셋·정책·컨트랙트 관리, 카탈로그 조회,
    컨트랙트 협상 폴링, 데이터 전송 시작 기능을 제공합니다.
    """

    def __init__(
        self,
        management_url: str,
        client: Optional[HttpJsonClient] = None,
        api_key: Optional[str] = None,
    ):
        self.management_url = management_url.rstrip("/")
        self.client = client or HttpJsonClient()
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    # ── 에셋 ──────────────────────────────────────────────────────────────────

    def register_asset(self, asset: EDCAsset) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/assets"
        result = self.client.request_json("POST", url, payload=asset.to_management_payload(), headers=self._headers())
        LOGGER.info("EDC 에셋 등록: %s", asset.asset_id)
        return result

    def list_assets(self, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"{self.management_url}/v3/assets/request"
        result = self.client.request_json("POST", url, payload={"limit": limit}, headers=self._headers())
        return result if isinstance(result, list) else result.get("items", [])

    def delete_asset(self, asset_id: str) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/assets/{asset_id}"
        return self.client.request_json("DELETE", url, headers=self._headers())

    # ── 정책 ──────────────────────────────────────────────────────────────────

    def create_policy(self, policy: EDCPolicy) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/policydefinitions"
        result = self.client.request_json("POST", url, payload=policy.to_management_payload(), headers=self._headers())
        LOGGER.info("정책 생성: %s", policy.policy_id)
        return result

    def list_policies(self, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"{self.management_url}/v3/policydefinitions/request"
        result = self.client.request_json("POST", url, payload={"limit": limit}, headers=self._headers())
        return result if isinstance(result, list) else result.get("items", [])

    # ── 컨트랙트 정의 ─────────────────────────────────────────────────────────

    def create_contract_definition(self, definition: ContractDefinition) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/contractdefinitions"
        result = self.client.request_json("POST", url, payload=definition.to_management_payload(), headers=self._headers())
        LOGGER.info("컨트랙트 정의 생성: %s", definition.contract_definition_id)
        return result

    # ── 카탈로그 조회 (소비자) ────────────────────────────────────────────────

    def request_catalog(
        self,
        counter_party_protocol_url: str,
        asset_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "counterPartyAddress": counter_party_protocol_url,
            "protocol": "dataspace-protocol-http",
        }
        if asset_id:
            payload["querySpec"] = {
                "filterExpression": [{
                    "operandLeft": "https://w3id.org/edc/v0.0.1/ns/id",
                    "operator": "=",
                    "operandRight": asset_id,
                }]
            }
        url = f"{self.management_url}/v3/catalog/request"
        return self.client.request_json("POST", url, payload=payload, headers=self._headers())

    # ── 컨트랙트 협상 (소비자) ────────────────────────────────────────────────

    def negotiate_contract(
        self,
        counter_party_protocol_url: str,
        asset_id: str,
        offer_id: str,
        provider_participant_id: str,
        consumer_participant_id: str,
        policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@type": "ContractRequest",
            "counterPartyAddress": counter_party_protocol_url,
            "protocol": "dataspace-protocol-http",
            "providerId": provider_participant_id,
            "connectorId": consumer_participant_id,
            "offer": {
                "@id": offer_id,
                "assetId": asset_id,
                "providerId": provider_participant_id,
            },
        }
        if policy:
            payload["offer"]["policy"] = policy

        url = f"{self.management_url}/v3/contractnegotiations"
        result = self.client.request_json("POST", url, payload=payload, headers=self._headers())
        negotiation_id = result.get("@id", result.get("id", ""))
        LOGGER.info("컨트랙트 협상 시작: %s", negotiation_id)
        return result

    def get_negotiation(self, negotiation_id: str) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/contractnegotiations/{negotiation_id}"
        return self.client.request_json("GET", url, headers=self._headers())

    def await_agreement(
        self,
        negotiation_id: str,
        poll_interval: float = 2.0,
        max_wait: float = 120.0,
    ) -> Tuple[str, str]:
        """협상 완료를 폴링으로 대기합니다. (agreement_id, state) 반환."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            neg = self.get_negotiation(negotiation_id)
            state: str = neg.get("state", neg.get("edc:state", ""))
            if state == "FINALIZED":
                agreement_id: str = neg.get("contractAgreementId", neg.get("edc:contractAgreementId", ""))
                LOGGER.info("협상 완료: agreement_id=%s", agreement_id)
                return agreement_id, state
            if state in {"TERMINATED", "ERROR"}:
                raise RuntimeError(f"컨트랙트 협상 실패: state={state}")
            LOGGER.debug("협상 대기 중: state=%s", state)
            time.sleep(poll_interval)
        raise TimeoutError(f"협상 타임아웃 ({max_wait}s): negotiation_id={negotiation_id}")

    # ── 데이터 전송 시작 (소비자) ─────────────────────────────────────────────

    def initiate_transfer(
        self,
        agreement_id: str,
        asset_id: str,
        counter_party_protocol_url: str,
        data_destination_url: str,
        provider_participant_id: str,
    ) -> Dict[str, Any]:
        """데이터 전송 프로세스를 시작합니다 (HTTP Pull 방식)."""
        payload = {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@type": "TransferRequest",
            "assetId": asset_id,
            "contractId": agreement_id,
            "connectorId": provider_participant_id,
            "counterPartyAddress": counter_party_protocol_url,
            "protocol": "dataspace-protocol-http",
            "transferType": "HttpData-PULL",
            "dataDestination": {
                "type": "HttpData",
                "baseUrl": data_destination_url,
            },
        }
        url = f"{self.management_url}/v3/transferprocesses"
        result = self.client.request_json("POST", url, payload=payload, headers=self._headers())
        LOGGER.info("데이터 전송 시작: agreement_id=%s", agreement_id)
        return result

    def get_transfer(self, transfer_id: str) -> Dict[str, Any]:
        url = f"{self.management_url}/v3/transferprocesses/{transfer_id}"
        return self.client.request_json("GET", url, headers=self._headers())


# ──────────────────────────────────────────────────────────────────────────────
# 파이프라인 (고수준 오케스트레이터)
# ──────────────────────────────────────────────────────────────────────────────

class CobotEDCPipeline:
    """EDC 에셋 온보딩 + AAS 동기화 + 협상·전송 워크플로 오케스트레이터.

    사용 예
    ────────
    pipeline = build_pipeline_from_env()

    # 공급자: 에셋 온보딩
    pipeline.onboard_cobot_asset("cobot-01-telemetry", "BPNL000000000001",
                                 "http://localhost:8080")

    # 공급자: AAS 동기화
    pipeline.publish_telemetry_to_aas(telemetry_dict)

    # 소비자: 데이터 수신 워크플로
    pipeline.consume_asset("http://provider:8282/api/v1/ids/data",
                           "cobot-01-telemetry", "BPNL000000000001", "BPNL000000000002")
    """

    def __init__(self, connector: EDCConnectorService, aas_bridge: AASBridge):
        self.connector = connector
        self.aas_bridge = aas_bridge

    # ── 공급자: 에셋 온보딩 ───────────────────────────────────────────────────

    def onboard_cobot_asset(
        self,
        asset_id: str,
        provider_bpn: str,
        cobot_api_base_url: str,
        cobot_data_path: str = "/api/v1/cobot/telemetry",
        policy_type: str = "bpn",
    ) -> Dict[str, Dict[str, Any]]:
        """EDC에 협동로봇 에셋, 정책, 컨트랙트를 일괄 등록합니다.

        policy_type: "bpn"(기본) | "membership" | "open"
        """
        asset = EDCAsset(
            asset_id=asset_id,
            name=f"Cobot 텔레메트리 {asset_id}",
            base_url=cobot_api_base_url,
            data_path=cobot_data_path,
            description="공장 협동로봇 실시간 운용 데이터 스트림 (AAS Submodel 연동)",
            provider_bpn=provider_bpn,
            asset_type="factory-cobot-telemetry",
            semantic_id=self.aas_bridge.COBOT_SEMANTIC_ID,
        )

        def _make_policy(suffix: str) -> EDCPolicy:
            pid = f"{asset_id}-{suffix}-policy"
            if policy_type == "membership":
                return EDCPolicy.membership(pid, asset_id)
            if policy_type == "open":
                return EDCPolicy.open_access(pid, asset_id)
            return EDCPolicy.bpn_restricted(pid, asset_id, provider_bpn)

        access_policy   = _make_policy("access")
        contract_policy = _make_policy("contract")
        contract = ContractDefinition(
            contract_definition_id=f"{asset_id}-contract",
            access_policy_id=access_policy.policy_id,
            contract_policy_id=contract_policy.policy_id,
            asset_id=asset_id,
        )

        LOGGER.info("온보딩 시작: asset_id=%s provider_bpn=%s policy=%s",
                    asset_id, provider_bpn, policy_type)
        return {
            "asset":               self.connector.register_asset(asset),
            "access_policy":       self.connector.create_policy(access_policy),
            "contract_policy":     self.connector.create_policy(contract_policy),
            "contract_definition": self.connector.create_contract_definition(contract),
        }

    # ── 공급자: AAS 동기화 ────────────────────────────────────────────────────

    def publish_telemetry_to_aas(
        self,
        telemetry: "Mapping[str, Any] | FactoryCobotTelemetry",
        ensure_shell: bool = True,
    ) -> Dict[str, Any]:
        """텔레메트리를 AAS Submodel로 변환해 서버에 업서트합니다."""
        if not isinstance(telemetry, FactoryCobotTelemetry):
            telemetry = FactoryCobotTelemetry.from_dict(telemetry)
        if ensure_shell:
            self.aas_bridge.ensure_shell(telemetry.robot_id)
        return self.aas_bridge.upsert_telemetry(telemetry)

    # ── 공급자: AAS Registry 등록 ─────────────────────────────────────────────

    def register_to_registry(
        self,
        robot_id: str,
        edc_data_plane_url: str,
        asset_id: str,
    ) -> Dict[str, Any]:
        """AAS 레지스트리에 Shell Descriptor를 등록합니다."""
        return self.aas_bridge.register_shell_descriptor(robot_id, edc_data_plane_url, asset_id)

    # ── 소비자: 카탈로그 조회 ─────────────────────────────────────────────────

    def query_provider_catalog(
        self,
        provider_protocol_url: str,
        asset_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.connector.request_catalog(provider_protocol_url, asset_id)

    # ── 소비자: 카탈로그 → 협상 → 전송 워크플로 ─────────────────────────────

    def consume_asset(
        self,
        provider_protocol_url: str,
        asset_id: str,
        provider_bpn: str,
        consumer_bpn: str,
        data_destination_url: str = "http://localhost:9191/consumer/store",
        poll_interval: float = 3.0,
        negotiation_timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """카탈로그 조회 → 협상 → 전송 시작 전체 워크플로.

        Returns {"agreement_id": ..., "transfer_id": ..., "transfer": {...}}
        """
        # 1) 카탈로그에서 offer 탐색
        LOGGER.info("카탈로그 조회: %s", provider_protocol_url)
        catalog = self.connector.request_catalog(provider_protocol_url, asset_id)
        datasets = catalog.get("dcat:dataset", catalog.get("dataset", []))
        if not isinstance(datasets, list):
            datasets = [datasets]

        offer_id: Optional[str] = None
        for ds in datasets:
            if ds.get("@id") == asset_id or ds.get("id") == asset_id:
                offers = ds.get("odrl:hasPolicy", ds.get("hasPolicy", []))
                if not isinstance(offers, list):
                    offers = [offers]
                if offers:
                    offer_id = offers[0].get("@id", offers[0].get("id"))
                break

        if not offer_id:
            raise RuntimeError(f"카탈로그에서 asset_id={asset_id} offer를 찾을 수 없습니다.")

        # 2) 컨트랙트 협상
        LOGGER.info("컨트랙트 협상 시작: offer_id=%s", offer_id)
        neg_result = self.connector.negotiate_contract(
            counter_party_protocol_url=provider_protocol_url,
            asset_id=asset_id,
            offer_id=offer_id,
            provider_participant_id=provider_bpn,
            consumer_participant_id=consumer_bpn,
        )
        negotiation_id: str = neg_result.get("@id", neg_result.get("id", ""))
        agreement_id, _ = self.connector.await_agreement(
            negotiation_id, poll_interval, negotiation_timeout
        )

        # 3) 데이터 전송 시작
        LOGGER.info("데이터 전송 시작: agreement_id=%s", agreement_id)
        transfer = self.connector.initiate_transfer(
            agreement_id=agreement_id,
            asset_id=asset_id,
            counter_party_protocol_url=provider_protocol_url,
            data_destination_url=data_destination_url,
            provider_participant_id=provider_bpn,
        )
        transfer_id: str = transfer.get("@id", transfer.get("id", ""))

        return {
            "agreement_id": agreement_id,
            "transfer_id": transfer_id,
            "transfer": transfer,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 환경변수 기반 파이프라인 팩토리
# ──────────────────────────────────────────────────────────────────────────────

def build_pipeline_from_env() -> CobotEDCPipeline:
    """환경변수로 CobotEDCPipeline을 생성합니다.

    필수
    ────
    CATENAX_EDC_MANAGEMENT_URL   http://localhost:9191/management
    CATENAX_AAS_BASE_URL         http://localhost:8081
    CATENAX_AAS_SUBMODEL_ID      urn:uuid:cobot-operational-data-submodel

    선택
    ────
    CATENAX_EDC_API_KEY
    CATENAX_AAS_API_KEY
    CATENAX_AAS_REGISTRY_URL     http://localhost:8082  (레지스트리가 별도인 경우)
    CATENAX_AAS_SHELL_ID         urn:uuid:cobot-01-shell
    CATENAX_HTTP_TIMEOUT         초 단위 (기본 15)
    CATENAX_MAX_RETRIES          기본 3
    """
    management_url = os.environ["CATENAX_EDC_MANAGEMENT_URL"]
    aas_base_url   = os.environ["CATENAX_AAS_BASE_URL"]
    submodel_id    = os.environ["CATENAX_AAS_SUBMODEL_ID"]
    edc_api_key    = os.environ.get("CATENAX_EDC_API_KEY")
    aas_api_key    = os.environ.get("CATENAX_AAS_API_KEY")
    registry_url   = os.environ.get("CATENAX_AAS_REGISTRY_URL", "")
    shell_id       = os.environ.get("CATENAX_AAS_SHELL_ID")
    timeout        = float(os.environ.get("CATENAX_HTTP_TIMEOUT", "15"))
    max_retries    = int(os.environ.get("CATENAX_MAX_RETRIES", "3"))

    http_client = HttpJsonClient(timeout=timeout, max_retries=max_retries)
    connector   = EDCConnectorService(management_url=management_url, client=http_client, api_key=edc_api_key)
    aas_bridge  = AASBridge(
        aas_base_url=aas_base_url,
        submodel_id=submodel_id,
        client=http_client,
        auth_key=aas_api_key,
        registry_url=registry_url,
        shell_id=shell_id,
    )
    return CobotEDCPipeline(connector=connector, aas_bridge=aas_bridge)


# ──────────────────────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────────────────────

def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Iterable[str]] = None) -> int:
    """CLI 진입점.

    커맨드
    ──────
    onboard   : EDC에 협동로봇 에셋·정책·컨트랙트 등록
    sync-aas  : 텔레메트리 JSON → AAS Submodel 동기화
    catalog   : 공급자 카탈로그 조회
    consume   : 카탈로그 → 협상 → 전송 워크플로
    register  : AAS Registry에 Shell Descriptor 등록

    환경변수 설정 예시
    ──────────────────
    export CATENAX_EDC_MANAGEMENT_URL=http://localhost:9191/management
    export CATENAX_AAS_BASE_URL=http://localhost:8081
    export CATENAX_AAS_SUBMODEL_ID=urn:uuid:cobot-operational-data-submodel
    export CATENAX_EDC_API_KEY=your-edc-api-key
    export CATENAX_AAS_API_KEY=your-aas-api-key
    """
    parser = argparse.ArgumentParser(
        description="Catena-X EDC 커넥터 – 협동로봇 AAS 연동",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # onboard
    p_on = sub.add_parser("onboard", help="EDC에 협동로봇 에셋 등록")
    p_on.add_argument("--asset-id", required=True)
    p_on.add_argument("--provider-bpn", required=True)
    p_on.add_argument("--cobot-api-base-url", required=True)
    p_on.add_argument("--cobot-data-path", default="/api/v1/cobot/telemetry")
    p_on.add_argument("--policy-type", choices=["bpn", "membership", "open"],
                      default="bpn", help="정책 유형 (기본: bpn)")

    # sync-aas
    p_sync = sub.add_parser("sync-aas", help="텔레메트리 JSON → AAS 동기화")
    p_sync.add_argument("--telemetry-json", required=True)
    p_sync.add_argument("--no-shell", action="store_true",
                        help="AAS Shell 자동 생성을 건너뜁니다")

    # catalog
    p_cat = sub.add_parser("catalog", help="공급자 카탈로그 조회")
    p_cat.add_argument("--provider-protocol-url", required=True)
    p_cat.add_argument("--asset-id")

    # consume
    p_con = sub.add_parser("consume", help="협상 + 데이터 전송 워크플로")
    p_con.add_argument("--provider-protocol-url", required=True)
    p_con.add_argument("--asset-id", required=True)
    p_con.add_argument("--provider-bpn", required=True)
    p_con.add_argument("--consumer-bpn", required=True)
    p_con.add_argument("--data-destination-url",
                       default="http://localhost:9191/consumer/store")

    # register
    p_reg = sub.add_parser("register", help="AAS Registry에 Shell Descriptor 등록")
    p_reg.add_argument("--robot-id", required=True)
    p_reg.add_argument("--edc-data-plane-url", required=True)
    p_reg.add_argument("--asset-id", required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    pipeline = build_pipeline_from_env()
    result: Any = {}

    if args.command == "onboard":
        result = pipeline.onboard_cobot_asset(
            asset_id=args.asset_id,
            provider_bpn=args.provider_bpn,
            cobot_api_base_url=args.cobot_api_base_url,
            cobot_data_path=args.cobot_data_path,
            policy_type=args.policy_type,
        )

    elif args.command == "sync-aas":
        raw = _load_json(args.telemetry_json)
        result = pipeline.publish_telemetry_to_aas(raw, ensure_shell=not args.no_shell)

    elif args.command == "catalog":
        result = pipeline.query_provider_catalog(
            provider_protocol_url=args.provider_protocol_url,
            asset_id=getattr(args, "asset_id", None),
        )

    elif args.command == "consume":
        result = pipeline.consume_asset(
            provider_protocol_url=args.provider_protocol_url,
            asset_id=args.asset_id,
            provider_bpn=args.provider_bpn,
            consumer_bpn=args.consumer_bpn,
            data_destination_url=args.data_destination_url,
        )

    elif args.command == "register":
        result = pipeline.register_to_registry(
            robot_id=args.robot_id,
            edc_data_plane_url=args.edc_data_plane_url,
            asset_id=args.asset_id,
        )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
