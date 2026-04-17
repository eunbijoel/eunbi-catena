"""ai_helpers.py — Ollama 기반 AI 보조 모듈 (Optional).

이 모듈은 다음 두 가지 AI 보조 기능을 제공합니다:

1. validate_with_ai(normalized)
   → 전처리 완료된 텔레메트리를 Ollama에 전달하여
     이상 징후·이상값 여부를 자연어로 분석

2. suggest_policy_with_ai(asset_id, provider_bpn)
   → EDC 정책 생성 시 Ollama에 정책 권고사항을 질의

설계 원칙
─────────
- Ollama를 사용할 수 없어도 시스템 전체가 정상 동작해야 합니다.
- 모든 AI 함수는 OllamaUnavailableError를 발생시키며,
  호출부에서 try/except로 graceful fallback 처리합니다.
- AI 응답은 참고용(advisory)이며 결정권은 항상 결정론적 로직에 있습니다.
- 모델 이름은 환경변수 OLLAMA_MODEL로 변경 가능합니다 (기본: llama3).

Ollama 설치 및 실행:
    $ ollama serve
    $ ollama pull llama3      # 또는 mistral, phi3 등

환경변수:
    OLLAMA_BASE_URL  = http://localhost:11434   (기본값)
    OLLAMA_MODEL     = llama3                  (기본값)
    OLLAMA_TIMEOUT   = 30                      (초 단위)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("catenax.ai_helpers")


# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────

def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

def _ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "llama3")

def _ollama_timeout() -> float:
    return float(os.environ.get("OLLAMA_TIMEOUT", "30"))


# ─────────────────────────────────────────────────────────────────────────────
# 에러 타입
# ─────────────────────────────────────────────────────────────────────────────

class OllamaUnavailableError(RuntimeError):
    """Ollama 서버에 연결할 수 없거나 모델이 로드되지 않은 경우."""


# ─────────────────────────────────────────────────────────────────────────────
# 저수준 HTTP 호출
# ─────────────────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    """Ollama /api/generate 엔드포인트를 호출하고 응답 텍스트를 반환합니다.

    Args:
        prompt: Ollama에 전달할 프롬프트 문자열

    Returns:
        모델의 텍스트 응답

    Raises:
        OllamaUnavailableError: 연결 실패, 타임아웃, HTTP 오류 시
    """
    url     = f"{_ollama_base_url()}/api/generate"
    payload = json.dumps({
        "model":  _ollama_model(),
        "prompt": prompt,
        "stream": False,         # 단일 응답 수신 (스트리밍 비활성화)
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_ollama_timeout()) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        return data.get("response", "").strip()

    except urllib.error.URLError as exc:
        raise OllamaUnavailableError(
            f"Ollama 서버({_ollama_base_url()})에 연결할 수 없습니다: {exc.reason}\n"
            "$ ollama serve 로 서버를 시작하세요."
        ) from exc
    except urllib.error.HTTPError as exc:
        raise OllamaUnavailableError(
            f"Ollama HTTP 오류 {exc.code}: {exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    except Exception as exc:
        raise OllamaUnavailableError(f"Ollama 호출 실패: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# AI 보조 함수
# ─────────────────────────────────────────────────────────────────────────────

def validate_with_ai(normalized_dict: Dict[str, Any]) -> Dict[str, Any]:
    """전처리 텔레메트리를 AI로 분석하여 이상 징후 의견을 반환합니다.

    결정론적 전처리(TelemetryPreprocessor)가 먼저 실행된 뒤,
    이 함수를 추가적인 자연어 분석에 사용합니다.

    Args:
        normalized_dict: NormalizedTelemetry.to_dict()의 반환값

    Returns:
        {
          "ai_assessment": str,   # 이상 징후 자연어 평가
          "ai_suggestion": str,   # 권고 조치
          "model": str,           # 사용된 모델명
          "ok": bool              # AI 호출 성공 여부
        }

    Raises:
        OllamaUnavailableError: Ollama 서버 미응답 시
    """
    # 프롬프트에 포함할 핵심 지표만 추출 (컨텍스트 절약)
    metrics = {
        "robot_id":      normalized_dict.get("robot_id"),
        "status":        normalized_dict.get("status"),
        "cycle_time_ms": normalized_dict.get("cycle_time_ms"),
        "power_watts":   normalized_dict.get("power_watts"),
        "temperature_c": normalized_dict.get("temperature_c"),
        "vibration_mm_s": normalized_dict.get("vibration_mm_s"),
        "yield_rate":    normalized_dict.get("yield_rate"),
        "quality_flag":  normalized_dict.get("quality_flag"),
        "alarms":        normalized_dict.get("alarms", []),
        "issues":        [
            i["message"] for i in normalized_dict.get("issues", [])
        ],
    }

    prompt = f"""You are a manufacturing quality engineer analyzing collaborative robot telemetry.
Analyze the following cobot telemetry and identify any anomalies or concerns.
Respond in Korean. Be concise (max 3 sentences per section).

Telemetry data:
{json.dumps(metrics, ensure_ascii=False, indent=2)}

Provide:
1. AI Assessment: Brief anomaly assessment
2. AI Suggestion: Recommended actions

Format:
AI Assessment: <your assessment>
AI Suggestion: <your suggestion>
"""

    LOGGER.info("Ollama AI 텔레메트리 분석 요청: robot_id=%s", normalized_dict.get("robot_id"))
    response = _call_ollama(prompt)

    # 응답 파싱
    assessment = _extract_section(response, "AI Assessment")
    suggestion = _extract_section(response, "AI Suggestion")

    LOGGER.info("Ollama 분석 완료")
    return {
        "ai_assessment": assessment or response,  # 파싱 실패 시 전체 응답 반환
        "ai_suggestion": suggestion or "없음",
        "model":         _ollama_model(),
        "ok":            True,
    }


def suggest_policy_with_ai(
    asset_id: str,
    provider_bpn: str,
    asset_description: str = "",
) -> Dict[str, Any]:
    """EDC 정책 선택에 대한 AI 권고를 반환합니다.

    Args:
        asset_id:          EDC 에셋 ID
        provider_bpn:      공급자 BPN
        asset_description: 에셋 설명 (선택)

    Returns:
        {
          "recommended_policy": str,    # "bpn" | "membership" | "open"
          "rationale": str,             # 권고 이유
          "model": str,
          "ok": bool
        }

    Raises:
        OllamaUnavailableError: Ollama 서버 미응답 시
    """
    prompt = f"""You are a data governance expert for Catena-X automotive data space.
Given the following EDC asset, recommend the most appropriate access policy.
Respond in Korean. Be concise.

Asset ID: {asset_id}
Provider BPN: {provider_bpn}
Description: {asset_description or "공장 협동로봇 텔레메트리 데이터"}

Available policy types:
- bpn: Restrict to specific Business Partner Number (most restrictive)
- membership: Allow all Catena-X members (moderate)
- open: No restrictions (development/testing only)

Respond with:
Recommended Policy: <bpn|membership|open>
Rationale: <your rationale>
"""

    LOGGER.info("Ollama 정책 권고 요청: asset_id=%s", asset_id)
    response = _call_ollama(prompt)

    policy_line = _extract_section(response, "Recommended Policy")
    rationale   = _extract_section(response, "Rationale")

    # 유효한 정책 유형인지 검사
    recommended = "bpn"   # 기본 fallback
    if policy_line:
        for ptype in ("bpn", "membership", "open"):
            if ptype in policy_line.lower():
                recommended = ptype
                break

    return {
        "recommended_policy": recommended,
        "rationale":          rationale or response,
        "model":              _ollama_model(),
        "ok":                 True,
    }


def check_ollama_available() -> bool:
    """Ollama 서버 가용 여부를 확인합니다 (health check).

    Returns:
        True  : 서버 응답 정상
        False : 연결 불가
    """
    try:
        url = f"{_ollama_base_url()}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _extract_section(text: str, label: str) -> str:
    """응답 텍스트에서 'Label: value' 형태의 값을 추출합니다."""
    for line in text.splitlines():
        if line.strip().startswith(f"{label}:"):
            return line.split(":", 1)[1].strip()
    return ""
