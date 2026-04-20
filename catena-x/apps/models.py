"""models.py — Catena-X 협동로봇 플랫폼 데이터 모델 정의.

이 파일은 프로젝트 전체에서 사용하는 핵심 데이터 구조를 정의합니다.
모든 다른 모듈(aas_mapper, edc, ai_helpers)은 여기서 정의된 타입에 의존합니다.

계층 구조
─────────
RawTelemetry          ← 협동로봇에서 들어오는 raw JSON 그대로
  ↓ 전처리
NormalizedTelemetry   ← 유효성 검증 + 단위 표준화 완료된 데이터
  ↓ 매핑
AASShell              ← AAS Shell (로봇 자산 자체를 표현)
  └─ AASSubmodel      ← AAS Submodel (운용 데이터를 표현)
       └─ SubmodelElementCollection  ← 계층 구조 SME

EDCAsset              ← EDC 에셋 정의
EDCPolicy             ← ODRL 정책
ContractDefinition    ← 에셋 + 정책을 묶는 컨트랙트
CatalogEntry          ← 로컬 카탈로그 항목 (공급자 제공 목록)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    """현재 UTC 시각을 ISO-8601 문자열로 반환."""
    return datetime.now(UTC).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Enum 정의
# ─────────────────────────────────────────────────────────────────────────────

class RobotStatus(str, Enum):
    """협동로봇 운용 상태.

    RUNNING  : 정상 가동 중
    IDLE     : 대기 (프로그램 로드됨, 미실행)
    ERROR    : 에러 발생
    STOP     : 정지 (비상정지 또는 수동정지)
    UNKNOWN  : 상태 불명 (통신 단절 등)
    """
    RUNNING = "RUNNING"
    IDLE    = "IDLE"
    ERROR   = "ERROR"
    STOP    = "STOP"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_raw(cls, value: str) -> "RobotStatus":
        """문자열에서 안전하게 변환. 알 수 없는 값은 UNKNOWN 반환."""
        try:
            return cls(value.upper())
        except ValueError:
            return cls.UNKNOWN


class PolicyType(str, Enum):
    """EDC 정책 유형.

    BPN_RESTRICTED : 특정 BPN(Business Partner Number)만 허용
    MEMBERSHIP     : Catena-X 멤버십 보유자 전체 허용
    OPEN           : 제약 없음 (내부 테스트·개발용)
    """
    BPN_RESTRICTED = "bpn"
    MEMBERSHIP     = "membership"
    OPEN           = "open"


class QualityFlag(str, Enum):
    """텔레메트리 전처리 품질 플래그.

    OK       : 유효한 데이터
    WARNING  : 임계값 경고 (정상 범위 초과)
    CRITICAL : 심각한 이상값 감지
    INVALID  : 파싱 불가 또는 필수 필드 누락
    """
    OK       = "OK"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"
    INVALID  = "INVALID"


# ─────────────────────────────────────────────────────────────────────────────
# 원시 텔레메트리 (입력)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawTelemetry:
    """협동로봇에서 수신한 원시 텔레메트리.

    sample_telemetry.json의 필드를 그대로 반영합니다.
    전처리 전 단계이므로 타입 보장이 없습니다.

    주의: 이 객체는 불변(immutable) 원본 데이터입니다.
          수정이 필요하면 NormalizedTelemetry를 사용하세요.
    """

    # 필수 필드 (없으면 InvalidTelemetryError 발생)
    robot_id:     str
    line_id:      str
    station_id:   str
    cycle_time_ms: float
    power_watts:  float
    program_name: str
    status:       str

    # 선택 필드 (없으면 기본값 사용)
    good_parts:       int               = 0
    reject_parts:     int               = 0
    temperature_c:    Optional[float]   = None
    vibration_mm_s:   Optional[float]   = None
    pose:             Dict[str, float]  = field(default_factory=dict)
    joint_positions_deg: Dict[str, float] = field(default_factory=dict)
    alarms:           List[str]         = field(default_factory=list)
    produced_at:      str               = field(default_factory=_utc_now)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "RawTelemetry":
        """딕셔너리에서 RawTelemetry 생성.

        필수 필드 누락 시 KeyError를 발생시켜
        상위 레이어에서 InvalidTelemetryError로 감쌀 수 있게 합니다.

        ``joint_positions_deg`` 는 ``{"j1": …}`` 또는 ``[…]`` (축 순서) 모두 허용.
        """
        jdeg = raw.get("joint_positions_deg")
        if jdeg is None:
            joint_positions_deg: Dict[str, float] = {}
        elif isinstance(jdeg, list):
            joint_positions_deg = {f"j{i + 1}": float(x) for i, x in enumerate(jdeg)}
        elif isinstance(jdeg, dict):
            joint_positions_deg = {str(k): float(v) for k, v in jdeg.items()}
        else:
            raise TypeError(
                f"joint_positions_deg는 dict 또는 list여야 합니다: {type(jdeg).__name__}",
            )

        return cls(
            robot_id      = str(raw["robot_id"]),
            line_id       = str(raw["line_id"]),
            station_id    = str(raw["station_id"]),
            cycle_time_ms = float(raw["cycle_time_ms"]),
            power_watts   = float(raw["power_watts"]),
            program_name  = str(raw["program_name"]),
            status        = str(raw["status"]),
            good_parts    = int(raw.get("good_parts", 0)),
            reject_parts  = int(raw.get("reject_parts", 0)),
            temperature_c = float(raw["temperature_c"]) if raw.get("temperature_c") is not None else None,
            vibration_mm_s= float(raw["vibration_mm_s"]) if raw.get("vibration_mm_s") is not None else None,
            pose          = {str(k): float(v) for k, v in raw.get("pose", {}).items()},
            joint_positions_deg = joint_positions_deg,
            alarms        = [str(a) for a in raw.get("alarms", [])],
            produced_at   = str(raw.get("produced_at", _utc_now())),
        )

    def to_dict(self) -> Dict[str, Any]:
        """직렬화."""
        return {
            "robot_id": self.robot_id,
            "line_id": self.line_id,
            "station_id": self.station_id,
            "cycle_time_ms": self.cycle_time_ms,
            "power_watts": self.power_watts,
            "program_name": self.program_name,
            "status": self.status,
            "good_parts": self.good_parts,
            "reject_parts": self.reject_parts,
            "temperature_c": self.temperature_c,
            "vibration_mm_s": self.vibration_mm_s,
            "pose": self.pose,
            "joint_positions_deg": self.joint_positions_deg,
            "alarms": self.alarms,
            "produced_at": self.produced_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 전처리된 텔레메트리 (표준화 완료)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """전처리 중 발견된 개별 품질 이슈."""
    field:    str          # 문제가 발생한 필드명
    message:  str          # 사람이 읽을 수 있는 설명
    severity: QualityFlag  # WARNING 또는 CRITICAL


@dataclass
class NormalizedTelemetry:
    """전처리·검증이 완료된 표준화 텔레메트리.

    RawTelemetry에서 아래 처리를 거쳐 생성됩니다:
    1. 상태 Enum 변환 (RobotStatus)
    2. 임계값 범위 검사 (온도, 진동, 사이클 타임)
    3. 품질 플래그 부여 (QualityFlag)
    4. 타임스탬프 ISO-8601 표준화
    5. 수율(yield_rate) 계산
    6. 이상값 자동 클램핑 (선택)

    이 객체가 AAS 매핑 및 EDC 등록의 입력이 됩니다.
    """

    # 원본 필드 (전처리 후)
    robot_id:          str
    line_id:           str
    station_id:        str
    cycle_time_ms:     float
    power_watts:       float
    program_name:      str
    status:            RobotStatus
    good_parts:        int
    reject_parts:      int
    temperature_c:     Optional[float]
    vibration_mm_s:    Optional[float]
    pose:              Dict[str, float]
    joint_positions_deg: Dict[str, float]
    alarms:            List[str]
    produced_at:       str

    # 전처리에서 파생된 필드
    quality_flag:      QualityFlag           = QualityFlag.OK
    issues:            List[ValidationIssue] = field(default_factory=list)
    yield_rate:        float                 = 0.0    # good / (good + reject)
    preprocessed_at:   str                  = field(default_factory=_utc_now)

    def has_alarm(self) -> bool:
        return len(self.alarms) > 0

    def is_healthy(self) -> bool:
        return self.quality_flag in (QualityFlag.OK, QualityFlag.WARNING)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "line_id": self.line_id,
            "station_id": self.station_id,
            "cycle_time_ms": self.cycle_time_ms,
            "power_watts": self.power_watts,
            "program_name": self.program_name,
            "status": self.status.value,
            "good_parts": self.good_parts,
            "reject_parts": self.reject_parts,
            "temperature_c": self.temperature_c,
            "vibration_mm_s": self.vibration_mm_s,
            "pose": self.pose,
            "joint_positions_deg": self.joint_positions_deg,
            "alarms": self.alarms,
            "produced_at": self.produced_at,
            "quality_flag": self.quality_flag.value,
            "issues": [{"field": i.field, "message": i.message, "severity": i.severity.value}
                       for i in self.issues],
            "yield_rate": self.yield_rate,
            "preprocessed_at": self.preprocessed_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AAS 데이터 모델 (IDTA-01002-3-0 기반)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AASSubmodelElement:
    """AAS Submodel Element (Property / SubmodelElementCollection).

    modelType이 'Property'이면 value를 직접 가집니다.
    modelType이 'SubmodelElementCollection'이면 children에 하위 SME 목록을 가집니다.
    """
    model_type: str         # "Property" | "SubmodelElementCollection"
    id_short:   str         # 짧은 식별자 (공백·특수문자 없음)
    value_type: str = ""    # "xs:string" | "xs:double" | "xs:integer" | "xs:boolean"
    value:      Any = None  # Property일 때 실제 값
    children:   List["AASSubmodelElement"] = field(default_factory=list)  # Collection일 때

    def to_dict(self) -> Dict[str, Any]:
        if self.model_type == "SubmodelElementCollection":
            return {
                "modelType": "SubmodelElementCollection",
                "idShort": self.id_short,
                "value": [c.to_dict() for c in self.children],
            }
        return {
            "modelType": "Property",
            "idShort": self.id_short,
            "valueType": self.value_type,
            "value": str(self.value) if self.value is not None else "",
        }


@dataclass
class AASSubmodel:
    """AAS Submodel (IDTA-02017 협동로봇 운용 데이터).

    하나의 Submodel은 한 가지 관점(운용 데이터)의 정보를 담습니다.
    여러 SubmodelElementCollection으로 구성됩니다.

    구조:
        CobotOperationalData (Submodel)
        ├── OperationalState        (Collection)
        ├── ProductionMetrics       (Collection)
        ├── KinematicState          (Collection)
        └── QualityAndDiagnostics   (Collection)
    """
    submodel_id:   str
    id_short:      str   = "CobotOperationalData"
    semantic_id:   str   = "https://admin-shell.io/idta/CobotOperationalData/1/0"
    elements:      List[AASSubmodelElement] = field(default_factory=list)
    created_at:    str   = field(default_factory=_utc_now)
    updated_at:    str   = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modelType": "Submodel",
            "id": self.submodel_id,
            "idShort": self.id_short,
            "semanticId": {
                "type": "ExternalReference",
                "keys": [{"type": "GlobalReference", "value": self.semantic_id}],
            },
            "submodelElements": [e.to_dict() for e in self.elements],
            "_meta": {
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            },
        }


@dataclass
class AASShell:
    """AAS Shell (협동로봇 자산 자체를 표현하는 디지털 트윈 껍데기).

    Shell은 실제 자산(협동로봇)을 나타내며,
    여러 Submodel에 대한 참조(reference)를 포함합니다.

    globalAssetId: 실제 물리 자산의 전역 식별자 (예: urn:cobot:cobot-01)
    """
    shell_id:       str
    id_short:       str
    global_asset_id: str
    submodel_refs:  List[str]       = field(default_factory=list)  # Submodel ID 목록
    description:    str             = ""
    manufacturer:   str             = ""
    created_at:     str             = field(default_factory=_utc_now)
    updated_at:     str             = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modelType": "AssetAdministrationShell",
            "id": self.shell_id,
            "idShort": self.id_short,
            "assetInformation": {
                "assetKind": "Instance",
                "globalAssetId": self.global_asset_id,
                "specificAssetIds": (
                    [{"name": "manufacturer", "value": self.manufacturer}]
                    if self.manufacturer else []
                ),
            },
            "description": [{"language": "ko", "text": self.description}],
            "submodels": [
                {"type": "ExternalReference", "keys": [{"type": "Submodel", "value": ref}]}
                for ref in self.submodel_refs
            ],
            "_meta": {
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# EDC 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EDCAsset:
    """EDC 데이터 에셋 정의.

    Catena-X 데이터스페이스에서 공유되는 데이터 단위입니다.
    하나의 에셋은 하나의 협동로봇 텔레메트리 스트림에 대응합니다.

    base_url + data_path = 실제 데이터 접근 URL (EDC HttpData 주소)
    semantic_id          = AAS Submodel SemanticId (데이터 표준 참조)
    """
    asset_id:     str
    name:         str
    description:  str
    base_url:     str
    data_path:    str
    content_type: str               = "application/json"
    provider_bpn: str               = ""
    asset_type:   str               = "factory-cobot-telemetry"
    semantic_id:  str               = "https://admin-shell.io/idta/CobotOperationalData/1/0"
    extra:        Dict[str, Any]    = field(default_factory=dict)
    registered_at: str              = field(default_factory=_utc_now)

    def to_management_payload(self) -> Dict[str, Any]:
        """EDC Management API v3 포맷으로 직렬화."""
        props: Dict[str, Any] = {
            "asset:prop:id":          self.asset_id,
            "asset:prop:name":        self.name,
            "asset:prop:contenttype": self.content_type,
            "asset:prop:description": self.description,
            "catenax:assetType":      self.asset_type,
            "aas:semanticId":         self.semantic_id,
        }
        if self.provider_bpn:
            props["catenax:providerBpn"] = self.provider_bpn
        props.update(self.extra)

        return {
            "@context": {
                "@vocab":    "https://w3id.org/edc/v0.0.1/ns/",
                "cx-common": "https://w3id.org/catenax/ontology/common#",
                "aas":       "https://admin-shell.io/aas/3/0/",
            },
            "@id": self.asset_id,
            "properties": props,
            "dataAddress": {
                "@type":          "HttpData",
                "type":           "HttpData",
                "baseUrl":        self.base_url.rstrip("/"),
                "path":           self.data_path,
                "proxyMethod":    "true",
                "proxyPath":      "true",
                "proxyQueryParams": "true",
                "proxyBody":      "true",
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """로컬 저장용 직렬화."""
        return {
            "asset_id":     self.asset_id,
            "name":         self.name,
            "description":  self.description,
            "base_url":     self.base_url,
            "data_path":    self.data_path,
            "content_type": self.content_type,
            "provider_bpn": self.provider_bpn,
            "asset_type":   self.asset_type,
            "semantic_id":  self.semantic_id,
            "extra":        self.extra,
            "registered_at": self.registered_at,
            "_edc_payload": self.to_management_payload(),
        }


@dataclass
class EDCPolicy:
    """ODRL 기반 EDC 정책 정의 (Catena-X ODRL Profile 준수).

    정책은 누가 데이터에 접근할 수 있는지를 정의합니다.
    팩토리 메서드로 3가지 표준 정책을 쉽게 생성할 수 있습니다:
    - bpn_restricted(): 특정 파트너만
    - membership():     Catena-X 멤버 전체
    - open_access():    제약 없음 (테스트용)
    """
    policy_id:   str
    target:      str
    policy_type: PolicyType
    permissions: List[Dict[str, Any]] = field(default_factory=list)
    created_at:  str                  = field(default_factory=_utc_now)

    # ── 팩토리 메서드 ──────────────────────────────────────────────────────────

    @classmethod
    def bpn_restricted(cls, policy_id: str, target: str, allowed_bpn: str) -> "EDCPolicy":
        """특정 BPN만 USE 액션을 허용하는 정책."""
        return cls(
            policy_id=policy_id, target=target,
            policy_type=PolicyType.BPN_RESTRICTED,
            permissions=[{
                "action": "USE",
                "target": target,
                "constraint": {
                    "leftOperand": "BusinessPartnerNumber",
                    "operator": "EQ",
                    "rightOperand": allowed_bpn,
                },
            }],
        )

    @classmethod
    def membership(cls, policy_id: str, target: str) -> "EDCPolicy":
        """Catena-X 멤버십 보유자 전체에게 허용하는 정책."""
        return cls(
            policy_id=policy_id, target=target,
            policy_type=PolicyType.MEMBERSHIP,
            permissions=[{
                "action": "USE",
                "target": target,
                "constraint": {
                    "leftOperand": "Membership",
                    "operator": "EQ",
                    "rightOperand": "active",
                },
            }],
        )

    @classmethod
    def open_access(cls, policy_id: str, target: str) -> "EDCPolicy":
        """접근 제약 없는 개방 정책 (개발·테스트 전용)."""
        return cls(
            policy_id=policy_id, target=target,
            policy_type=PolicyType.OPEN,
            permissions=[{"action": "USE", "target": target}],
        )

    def to_management_payload(self) -> Dict[str, Any]:
        """EDC Management API v3 포맷으로 직렬화."""
        return {
            "@context": {
                "@vocab": "https://w3id.org/edc/v0.0.1/ns/",
                "odrl":   "http://www.w3.org/ns/odrl/2/",
            },
            "@id":   self.policy_id,
            "@type": "PolicyDefinition",
            "policy": {
                "@context": "http://www.w3.org/ns/odrl.jsonld",
                "@type":    "Set",
                "permission": self.permissions,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id":   self.policy_id,
            "target":      self.target,
            "policy_type": self.policy_type.value,
            "permissions": self.permissions,
            "created_at":  self.created_at,
            "_edc_payload": self.to_management_payload(),
        }


@dataclass
class ContractDefinition:
    """EDC 컨트랙트 정의 — 에셋 선택기 + 정책을 묶는 단위.

    하나의 ContractDefinition은 다음을 연결합니다:
    - 어떤 에셋을 (assetsSelector: asset_id)
    - 누가 접근할 수 있는지 (accessPolicyId)
    - 어떤 계약 조건으로 (contractPolicyId)

    실제 EDC에서는 이 정의를 통해 contract offer가 생성되고
    카탈로그에 노출됩니다.
    """
    contract_definition_id: str
    access_policy_id:        str
    contract_policy_id:      str
    asset_id:                str
    created_at:              str = field(default_factory=_utc_now)

    def to_management_payload(self) -> Dict[str, Any]:
        return {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@id":   self.contract_definition_id,
            "@type": "ContractDefinition",
            "accessPolicyId":   self.access_policy_id,
            "contractPolicyId": self.contract_policy_id,
            "assetsSelector": [{
                "@type":       "Criterion",
                "operandLeft": "https://w3id.org/edc/v0.0.1/ns/id",
                "operator":    "=",
                "operandRight": self.asset_id,
            }],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_definition_id": self.contract_definition_id,
            "access_policy_id":       self.access_policy_id,
            "contract_policy_id":     self.contract_policy_id,
            "asset_id":               self.asset_id,
            "created_at":             self.created_at,
            "_edc_payload":           self.to_management_payload(),
        }


@dataclass
class CatalogEntry:
    """로컬 카탈로그 항목 (공급자가 노출하는 데이터 목록).

    실제 Catena-X에서는 EDC 카탈로그 API를 통해 조회됩니다.
    이 구현에서는 로컬 JSON 파일로 시뮬레이션합니다.

    소비자는 이 카탈로그를 통해 어떤 에셋이 있고,
    어떤 정책(policy_type)으로 접근 가능한지 확인합니다.
    """
    asset_id:       str
    asset_name:     str
    provider_bpn:   str
    contract_id:    str
    semantic_id:    str
    description:    str  = ""
    robot_id:       str  = ""
    policy_type:    str  = "bpn"       # "bpn" | "membership" | "open"
    access_policy_id:   str = ""       # 접근 정책 ID (소비자 협상 시 참조)
    contract_policy_id: str = ""       # 계약 정책 ID
    last_updated:   str  = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id":          self.asset_id,
            "asset_name":        self.asset_name,
            "provider_bpn":      self.provider_bpn,
            "contract_id":       self.contract_id,
            "semantic_id":       self.semantic_id,
            "description":       self.description,
            "robot_id":          self.robot_id,
            "policy_type":       self.policy_type,
            "access_policy_id":  self.access_policy_id,
            "contract_policy_id": self.contract_policy_id,
            "last_updated":      self.last_updated,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 에러 타입
# ─────────────────────────────────────────────────────────────────────────────

class InvalidTelemetryError(ValueError):
    """필수 필드 누락 또는 타입 오류 등 텔레메트리 파싱 실패."""

class PreprocessingError(RuntimeError):
    """전처리 중 복구 불가능한 오류."""

class AASStoreError(IOError):
    """AAS 로컬 저장소 읽기/쓰기 오류."""

class EDCStoreError(IOError):
    """EDC 로컬 저장소 읽기/쓰기 오류."""
