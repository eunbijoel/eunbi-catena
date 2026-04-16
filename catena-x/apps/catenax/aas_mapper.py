"""aas_mapper.py — 협동로봇 텔레메트리 전처리 및 AAS 매핑 엔진.

이 모듈은 두 가지 핵심 역할을 담당합니다:

1. 전처리(Preprocessor)
   raw telemetry → NormalizedTelemetry
   - 필수 필드 유효성 검사
   - 임계값 기반 품질 플래그 부여 (OK / WARNING / CRITICAL)
   - 수율(yield_rate) 계산
   - 타임스탬프 표준화

2. AAS 매핑(AASMapper)
   NormalizedTelemetry → AASShell + AASSubmodel
   - IDTA-02017 협동로봇 Submodel 구조에 따라 SMC 계층 생성
   - SubmodelElementCollection 5개 그룹:
     · OperationalState     : 상태, 프로그램, 식별자
     · ProductionMetrics    : 생산 수량, 사이클 타임, 전력
     · KinematicState       : 포즈, 관절 위치
     · QualityAndDiagnostics: 품질 플래그, 알람, 수율

설계 원칙
─────────
- Preprocessor와 AASMapper는 완전히 분리되어 독립 테스트 가능
- 임계값은 TelemetryThresholds 데이터클래스로 외부에서 주입 가능
- 모든 함수는 순수 함수(side-effect 없음)
- 변환 결과의 AAS 구조는 to_dict()로 직렬화 가능
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from models import (
    AASShell,
    AASSubmodel,
    AASSubmodelElement,
    InvalidTelemetryError,
    NormalizedTelemetry,
    PreprocessingError,
    QualityFlag,
    RawTelemetry,
    RobotStatus,
    ValidationIssue,
)


# ─────────────────────────────────────────────────────────────────────────────
# 임계값 설정 (외부 주입 가능)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TelemetryThresholds:
    """텔레메트리 품질 판단 임계값.

    실제 현장에서는 로봇 모델·공정에 따라 다르게 설정합니다.
    환경변수나 설정 파일로 오버라이드 가능하게 설계되어 있습니다.
    """
    # 온도 (°C)
    temp_warn_c:     float = 55.0     # 이 온도 초과 시 WARNING
    temp_critical_c: float = 70.0    # 이 온도 초과 시 CRITICAL

    # 진동 (mm/s RMS)
    vibration_warn_mm_s:     float = 3.0
    vibration_critical_mm_s: float = 6.0

    # 사이클 타임 (ms) — 너무 빠르거나 너무 느린 경우
    cycle_time_min_ms: float = 500.0
    cycle_time_max_ms: float = 30_000.0

    # 소비 전력 (W)
    power_max_w: float = 2_000.0

    # 불량률 임계값 (reject / total)
    reject_rate_warn:     float = 0.05   # 5% 초과 시 WARNING
    reject_rate_critical: float = 0.15   # 15% 초과 시 CRITICAL

    @classmethod
    def default(cls) -> "TelemetryThresholds":
        return cls()


# ─────────────────────────────────────────────────────────────────────────────
# 전처리기
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryPreprocessor:
    """RawTelemetry → NormalizedTelemetry 변환기.

    사용 예:
        preprocessor = TelemetryPreprocessor()
        normalized = preprocessor.process(raw_telemetry)

    임계값 커스터마이징:
        thresholds = TelemetryThresholds(temp_warn_c=60.0)
        preprocessor = TelemetryPreprocessor(thresholds=thresholds)
    """

    def __init__(self, thresholds: Optional[TelemetryThresholds] = None):
        self.thresholds = thresholds or TelemetryThresholds.default()

    def process(self, raw: RawTelemetry) -> NormalizedTelemetry:
        """전처리 메인 함수.

        흐름:
        1. 상태 문자열 → RobotStatus Enum 변환
        2. 타임스탬프 표준화
        3. 수율 계산
        4. 임계값 기반 품질 이슈 수집
        5. 최종 QualityFlag 결정 (이슈 중 가장 심각한 것)
        """
        issues: List[ValidationIssue] = []

        # ── 1. 상태 변환 ──────────────────────────────────────────────────────
        status = RobotStatus.from_raw(raw.status)
        if status == RobotStatus.UNKNOWN:
            issues.append(ValidationIssue(
                field="status",
                message=f"알 수 없는 상태값 '{raw.status}' → UNKNOWN으로 처리",
                severity=QualityFlag.WARNING,
            ))

        # ── 2. 타임스탬프 표준화 ──────────────────────────────────────────────
        produced_at = self._normalize_timestamp(raw.produced_at)

        # ── 3. 수율 계산 ──────────────────────────────────────────────────────
        total_parts = raw.good_parts + raw.reject_parts
        yield_rate  = (raw.good_parts / total_parts) if total_parts > 0 else 0.0

        # ── 4. 임계값 검사 ────────────────────────────────────────────────────
        issues += self._check_temperature(raw.temperature_c)
        issues += self._check_vibration(raw.vibration_mm_s)
        issues += self._check_cycle_time(raw.cycle_time_ms)
        issues += self._check_power(raw.power_watts)
        issues += self._check_reject_rate(raw.reject_parts, total_parts)
        issues += self._check_alarms(raw.alarms)
        issues += self._check_error_status(status)

        # ── 5. 최종 품질 플래그 결정 ─────────────────────────────────────────
        quality_flag = self._derive_quality_flag(issues)

        return NormalizedTelemetry(
            robot_id          = raw.robot_id,
            line_id           = raw.line_id,
            station_id        = raw.station_id,
            cycle_time_ms     = raw.cycle_time_ms,
            power_watts       = raw.power_watts,
            program_name      = raw.program_name,
            status            = status,
            good_parts        = raw.good_parts,
            reject_parts      = raw.reject_parts,
            temperature_c     = raw.temperature_c,
            vibration_mm_s    = raw.vibration_mm_s,
            pose              = raw.pose,
            joint_positions_deg = raw.joint_positions_deg,
            alarms            = raw.alarms,
            produced_at       = produced_at,
            quality_flag      = quality_flag,
            issues            = issues,
            yield_rate        = round(yield_rate, 4),
        )

    # ── 임계값 검사 메서드 ────────────────────────────────────────────────────

    def _check_temperature(self, temp: Optional[float]) -> List[ValidationIssue]:
        if temp is None:
            return []
        t = self.thresholds
        if temp > t.temp_critical_c:
            return [ValidationIssue("temperature_c",
                f"온도 {temp}°C가 임계값 {t.temp_critical_c}°C 초과", QualityFlag.CRITICAL)]
        if temp > t.temp_warn_c:
            return [ValidationIssue("temperature_c",
                f"온도 {temp}°C가 경고 임계값 {t.temp_warn_c}°C 초과", QualityFlag.WARNING)]
        return []

    def _check_vibration(self, vib: Optional[float]) -> List[ValidationIssue]:
        if vib is None:
            return []
        t = self.thresholds
        if vib > t.vibration_critical_mm_s:
            return [ValidationIssue("vibration_mm_s",
                f"진동 {vib}mm/s가 임계값 {t.vibration_critical_mm_s}mm/s 초과", QualityFlag.CRITICAL)]
        if vib > t.vibration_warn_mm_s:
            return [ValidationIssue("vibration_mm_s",
                f"진동 {vib}mm/s가 경고 임계값 {t.vibration_warn_mm_s}mm/s 초과", QualityFlag.WARNING)]
        return []

    def _check_cycle_time(self, ms: float) -> List[ValidationIssue]:
        t = self.thresholds
        if ms < t.cycle_time_min_ms:
            return [ValidationIssue("cycle_time_ms",
                f"사이클 타임 {ms}ms가 최솟값 {t.cycle_time_min_ms}ms 미만", QualityFlag.WARNING)]
        if ms > t.cycle_time_max_ms:
            return [ValidationIssue("cycle_time_ms",
                f"사이클 타임 {ms}ms가 최댓값 {t.cycle_time_max_ms}ms 초과", QualityFlag.WARNING)]
        return []

    def _check_power(self, watts: float) -> List[ValidationIssue]:
        if watts > self.thresholds.power_max_w:
            return [ValidationIssue("power_watts",
                f"소비 전력 {watts}W가 최대 허용값 {self.thresholds.power_max_w}W 초과",
                QualityFlag.WARNING)]
        return []

    def _check_reject_rate(self, reject: int, total: int) -> List[ValidationIssue]:
        if total == 0:
            return []
        rate = reject / total
        t = self.thresholds
        if rate > t.reject_rate_critical:
            return [ValidationIssue("reject_parts",
                f"불량률 {rate:.1%}가 임계값 {t.reject_rate_critical:.1%} 초과", QualityFlag.CRITICAL)]
        if rate > t.reject_rate_warn:
            return [ValidationIssue("reject_parts",
                f"불량률 {rate:.1%}가 경고 임계값 {t.reject_rate_warn:.1%} 초과", QualityFlag.WARNING)]
        return []

    def _check_alarms(self, alarms: List[str]) -> List[ValidationIssue]:
        if alarms:
            return [ValidationIssue("alarms",
                f"{len(alarms)}개 알람 활성: {', '.join(alarms)}", QualityFlag.WARNING)]
        return []

    def _check_error_status(self, status: RobotStatus) -> List[ValidationIssue]:
        if status == RobotStatus.ERROR:
            return [ValidationIssue("status", "로봇 에러 상태", QualityFlag.CRITICAL)]
        if status == RobotStatus.STOP:
            return [ValidationIssue("status", "로봇 정지 상태", QualityFlag.WARNING)]
        return []

    @staticmethod
    def _derive_quality_flag(issues: List[ValidationIssue]) -> QualityFlag:
        """이슈 목록 중 가장 심각한 것을 최종 품질 플래그로 사용."""
        if any(i.severity == QualityFlag.CRITICAL for i in issues):
            return QualityFlag.CRITICAL
        if any(i.severity == QualityFlag.WARNING for i in issues):
            return QualityFlag.WARNING
        return QualityFlag.OK

    @staticmethod
    def _normalize_timestamp(ts: str) -> str:
        """타임스탬프를 UTC ISO-8601 형식으로 표준화.

        입력 형식 지원: ISO-8601 (Z, +00:00, 오프셋 포함/없음)
        파싱 실패 시 현재 시각으로 대체합니다.
        """
        try:
            # Z → +00:00 치환 후 파싱
            normalized = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            return dt.astimezone(UTC).isoformat()
        except (ValueError, AttributeError):
            return datetime.now(UTC).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# AAS 매퍼
# ─────────────────────────────────────────────────────────────────────────────

class AASMapper:
    """NormalizedTelemetry → AASShell + AASSubmodel 변환기.

    IDTA-02017 협동로봇 Submodel Template을 기반으로
    SubmodelElementCollection 계층 구조를 생성합니다.

    생성하는 SMC 구조:
    ─────────────────────────────────────────────────────────
    CobotOperationalData (Submodel)
    ├── OperationalState (Collection)
    │   ├── RobotId, LineId, StationId, Status, ProgramName
    │   └── ProducedAt
    ├── ProductionMetrics (Collection)
    │   ├── CycleTimeMs, PowerWatts, GoodParts, RejectParts
    │   ├── YieldRate
    │   └── TemperatureC, VibrationMmPerSec (선택)
    ├── KinematicState (Collection)
    │   ├── EndEffectorPose (Collection: X, Y, Z, RX, RY, RZ)
    │   └── JointPositions (Collection: J1..J6)
    └── QualityAndDiagnostics (Collection)
        ├── QualityFlag, AlarmCount, Alarms
        ├── PreprocessedAt
        └── Issue_N (각 이슈 항목)
    """

    # Submodel SemanticId (IDTA-02017)
    COBOT_SEMANTIC_ID = "https://admin-shell.io/idta/CobotOperationalData/1/0"

    def build_shell_and_submodel(
        self,
        normalized: NormalizedTelemetry,
        shell_id: Optional[str] = None,
        submodel_id: Optional[str] = None,
    ) -> Tuple[AASShell, AASSubmodel]:
        """Shell과 Submodel을 함께 생성합니다.

        Args:
            normalized:   전처리 완료된 텔레메트리
            shell_id:     Shell ID (미지정 시 자동 생성: urn:cobot:shell:{robot_id})
            submodel_id:  Submodel ID (미지정 시 자동 생성: urn:cobot:sm:{robot_id})

        Returns:
            (AASShell, AASSubmodel) 튜플
        """
        rid = normalized.robot_id
        _shell_id    = shell_id    or f"urn:cobot:shell:{rid}"
        _submodel_id = submodel_id or f"urn:cobot:sm:{rid}"

        shell = AASShell(
            shell_id        = _shell_id,
            id_short        = f"Cobot_{_safe_id_short(rid)}",
            global_asset_id = f"urn:cobot:{rid}",
            submodel_refs   = [_submodel_id],
            description     = f"협동로봇 {rid} AAS Shell — {normalized.line_id}/{normalized.station_id}",
        )

        submodel = self.build_submodel(normalized, _submodel_id)
        return shell, submodel

    def build_submodel(
        self,
        normalized: NormalizedTelemetry,
        submodel_id: Optional[str] = None,
    ) -> AASSubmodel:
        """Submodel만 생성합니다. (Shell 없이 Submodel 갱신 시 사용)"""
        _submodel_id = submodel_id or f"urn:cobot:sm:{normalized.robot_id}"

        elements = [
            self._build_operational_state(normalized),
            self._build_production_metrics(normalized),
            self._build_kinematic_state(normalized),
            self._build_quality_and_diagnostics(normalized),
        ]

        return AASSubmodel(
            submodel_id = _submodel_id,
            id_short    = "CobotOperationalData",
            semantic_id = self.COBOT_SEMANTIC_ID,
            elements    = elements,
        )

    # ── SMC 빌더 ─────────────────────────────────────────────────────────────

    def _build_operational_state(self, n: NormalizedTelemetry) -> AASSubmodelElement:
        """OperationalState SMC: 로봇 식별·상태·프로그램 정보."""
        return _collection("OperationalState", [
            _prop("RobotId",    n.robot_id,        "xs:string"),
            _prop("LineId",     n.line_id,          "xs:string"),
            _prop("StationId",  n.station_id,       "xs:string"),
            _prop("Status",     n.status.value,     "xs:string"),
            _prop("ProgramName", n.program_name,    "xs:string"),
            _prop("ProducedAt", n.produced_at,      "xs:dateTime"),
        ])

    def _build_production_metrics(self, n: NormalizedTelemetry) -> AASSubmodelElement:
        """ProductionMetrics SMC: 생산 수량·에너지·품질 지표."""
        children = [
            _prop("CycleTimeMs",  n.cycle_time_ms,  "xs:double"),
            _prop("PowerWatts",   n.power_watts,    "xs:double"),
            _prop("GoodParts",    n.good_parts,     "xs:integer"),
            _prop("RejectParts",  n.reject_parts,   "xs:integer"),
            _prop("YieldRate",    n.yield_rate,     "xs:double"),
        ]
        if n.temperature_c is not None:
            children.append(_prop("TemperatureC",   n.temperature_c,   "xs:double"))
        if n.vibration_mm_s is not None:
            children.append(_prop("VibrationMmPerSec", n.vibration_mm_s, "xs:double"))
        return _collection("ProductionMetrics", children)

    def _build_kinematic_state(self, n: NormalizedTelemetry) -> AASSubmodelElement:
        """KinematicState SMC: 엔드이펙터 포즈 + 관절 위치."""
        # 엔드이펙터 포즈 서브컬렉션
        pose_children = [
            _prop(axis.upper(), val, "xs:double")
            for axis, val in n.pose.items()
        ] if n.pose else [_prop("NotAvailable", "N/A", "xs:string")]

        # 관절 위치 서브컬렉션
        joint_children = [
            _prop(j.upper(), v, "xs:double")
            for j, v in n.joint_positions_deg.items()
        ] if n.joint_positions_deg else [_prop("NotAvailable", "N/A", "xs:string")]

        return _collection("KinematicState", [
            _collection("EndEffectorPose", pose_children),
            _collection("JointPositionsDeg", joint_children),
        ])

    def _build_quality_and_diagnostics(self, n: NormalizedTelemetry) -> AASSubmodelElement:
        """QualityAndDiagnostics SMC: 품질 플래그·알람·전처리 이슈."""
        children: List[AASSubmodelElement] = [
            _prop("QualityFlag",     n.quality_flag.value, "xs:string"),
            _prop("AlarmCount",      len(n.alarms),        "xs:integer"),
            _prop("Alarms",          ", ".join(n.alarms) if n.alarms else "none", "xs:string"),
            _prop("PreprocessedAt",  n.preprocessed_at,    "xs:dateTime"),
        ]
        # 각 ValidationIssue를 개별 Property로 추가
        for i, issue in enumerate(n.issues):
            children.append(_prop(
                f"Issue_{i+1:02d}",
                f"[{issue.severity.value}] {issue.field}: {issue.message}",
                "xs:string",
            ))
        return _collection("QualityAndDiagnostics", children)


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼 함수
# ─────────────────────────────────────────────────────────────────────────────

def _prop(id_short: str, value: Any, value_type: str = "xs:string") -> AASSubmodelElement:
    """Property SME 생성 헬퍼."""
    return AASSubmodelElement(
        model_type  = "Property",
        id_short    = id_short,
        value_type  = value_type,
        value       = value,
    )


def _collection(id_short: str, children: List[AASSubmodelElement]) -> AASSubmodelElement:
    """SubmodelElementCollection SME 생성 헬퍼."""
    return AASSubmodelElement(
        model_type = "SubmodelElementCollection",
        id_short   = id_short,
        children   = children,
    )


def _safe_id_short(value: str) -> str:
    """idShort에 허용되지 않는 문자를 밑줄로 치환."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


# ─────────────────────────────────────────────────────────────────────────────
# 편의 함수 (외부에서 직접 호출 가능)
# ─────────────────────────────────────────────────────────────────────────────

def preprocess(raw: RawTelemetry, thresholds: Optional[TelemetryThresholds] = None) -> NormalizedTelemetry:
    """RawTelemetry → NormalizedTelemetry 단일 호출 편의 함수."""
    return TelemetryPreprocessor(thresholds).process(raw)


def map_to_aas(
    normalized: NormalizedTelemetry,
    shell_id: Optional[str] = None,
    submodel_id: Optional[str] = None,
) -> Tuple[AASShell, AASSubmodel]:
    """NormalizedTelemetry → (AASShell, AASSubmodel) 단일 호출 편의 함수."""
    return AASMapper().build_shell_and_submodel(normalized, shell_id, submodel_id)
