"""업로드 파일이 디스크에 가는 위치 (JSON DB와 분리)."""

from __future__ import annotations

import os
from pathlib import Path


def uploads_root() -> Path:
    r = os.environ.get("KMX_DATA_DIR", "").strip()
    if r:
        p = Path(r).expanduser() / "uploads"
    else:
        p = Path(__file__).resolve().parents[1] / "data" / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p
