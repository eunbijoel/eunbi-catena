"""
데이터를 어디에 저장하나?

- 이 PC의 기본 python3 에는 가끔 SQLite 모듈이 빠져 있어서, 첫 MVP는 **JSON 파일 하나**에 전부 넣습니다.
- 나중에 PostgreSQL/SQLite 로 바꿀 때는 이 파일의 읽기/쓰기 부분만 갈아끼우면 됩니다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_data_path() -> Path:
    root = Path(os.environ.get("KMX_DATA_DIR", "")).expanduser()
    if root and root.is_dir():
        return root / "hub_state.json"
    return Path(__file__).resolve().parents[1] / "data" / "hub_state.json"


class JsonStore:
    """스레드 하나에서만 쓴다고 가정하고, 락으로 파일 읽기/쓰기만 보호."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _default_data_path()
        self._lock = threading.Lock()

    def _empty(self) -> Dict[str, Any]:
        return {
            "next_ids": {
                "tenant": 1,
                "user": 1,
                "dataset": 1,
                "audit": 1,
                "upload": 1,
                "connection": 1,
            },
            "tenants": [],
            "users": [],
            "memberships": [],
            "datasets": [],
            "audit_events": [],
            "uploads": [],
            "connection_profiles": [],
        }

    def load(self) -> Dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            return self._empty()
        with self.path.open(encoding="utf-8") as f:
            data = json.load(f)
        empty = self._empty()
        for key in empty:
            if key not in data:
                data[key] = empty[key]
        if isinstance(data.get("next_ids"), dict):
            for k, v in empty["next_ids"].items():
                if k not in data["next_ids"]:
                    data["next_ids"][k] = v
        return data

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)

    def mutate(self, fn):
        """fn(state) -> None; state 를 직접 수정."""
        with self._lock:
            state = self.load()
            fn(state)
            self.save(state)

    def read(self) -> Dict[str, Any]:
        with self._lock:
            return self.load()


def next_id(state: Dict[str, Any], name: str) -> int:
    n = int(state["next_ids"][name])
    state["next_ids"][name] = n + 1
    return n


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def find_tenant(state: Dict[str, Any], tenant_id: int) -> Optional[Dict[str, Any]]:
    for t in state["tenants"]:
        if int(t["id"]) == int(tenant_id):
            return t
    return None


def find_user_by_email(state: Dict[str, Any], email: str) -> Optional[Dict[str, Any]]:
    e = email.strip().lower()
    for u in state["users"]:
        if u["email"].lower() == e:
            return u
    return None


def find_dataset(state: Dict[str, Any], dataset_id: int) -> Optional[Dict[str, Any]]:
    for d in state["datasets"]:
        if int(d["id"]) == int(dataset_id):
            return d
    return None


def append_audit(
    state: Dict[str, Any],
    *,
    tenant_id: Optional[int],
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    aid = next_id(state, "audit")
    state["audit_events"].append(
        {
            "id": aid,
            "tenant_id": tenant_id,
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "detail": detail or {},
            "created_at": now_iso(),
        }
    )
