"""
비즈니스 규칙만 모아 둔 파일.

- 라우터(HTTP)는 ``main.py`` 에서 이 함수들만 호출합니다.
- 저장은 항상 ``store.mutate`` 로 묶어서 파일이 반쯤만 써지는 일을 줄입니다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from . import store as S
from .store import JsonStore, append_audit, find_dataset, find_tenant, find_user_by_email, next_id, now_iso


def register_tenant(store: JsonStore, *, legal_name: str, business_reg_no: str, contact_email: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        br = business_reg_no.strip()
        for t in state["tenants"]:
            if t["business_reg_no"] == br:
                raise HTTPException(status_code=409, detail="이미 같은 사업자번호로 등록됨")
        tid = next_id(state, "tenant")
        row = {
            "id": tid,
            "legal_name": legal_name.strip(),
            "business_reg_no": br,
            "contact_email": contact_email.strip().lower(),
            "status": "pending",
            "bpn": None,
            "created_at": S.now_iso(),
        }
        state["tenants"].append(row)
        append_audit(
            state,
            tenant_id=tid,
            actor=contact_email,
            action="tenant.register",
            resource_type="tenant",
            resource_id=str(tid),
            detail={"legal_name": legal_name},
        )
        out.clear()
        out.update(row)

    store.mutate(_fn)
    return out


def approve_tenant(store: JsonStore, tenant_id: int, *, bpn: Optional[str], actor: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        t = S.find_tenant(state, tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail="tenant 없음")
        if t["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"승인 불가 상태: {t['status']}")
        t["status"] = "active"
        t["bpn"] = (bpn or "").strip() or None
        append_audit(
            state,
            tenant_id=tenant_id,
            actor=actor,
            action="tenant.approve",
            resource_type="tenant",
            resource_id=str(tenant_id),
            detail={"bpn": t["bpn"]},
        )
        out.clear()
        out.update(t)

    store.mutate(_fn)
    return out


def add_user_to_tenant(
    store: JsonStore,
    tenant_id: int,
    *,
    email: str,
    display_name: str,
    role: str,
    actor: str,
) -> Dict[str, Any]:
    """Tenant 가 active 일 때만. user 없으면 만들고 membership 추가."""

    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        t = find_tenant(state, tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail="tenant 없음")
        if t["status"] != "active":
            raise HTTPException(status_code=400, detail="tenant 가 active 가 아니면 사용자 추가 불가")

        em = email.strip().lower()
        u = find_user_by_email(state, em)
        if not u:
            uid = next_id(state, "user")
            u = {"id": uid, "email": em, "display_name": display_name.strip() or em, "created_at": now_iso()}
            state["users"].append(u)

        uid = int(u["id"])
        for m in state["memberships"]:
            if int(m["user_id"]) == uid and int(m["tenant_id"]) == tenant_id:
                raise HTTPException(status_code=409, detail="이미 이 테넌트에 속한 사용자")

        state["memberships"].append({"user_id": uid, "tenant_id": tenant_id, "role": role})
        append_audit(
            state,
            tenant_id=tenant_id,
            actor=actor,
            action="membership.add",
            resource_type="user",
            resource_id=str(uid),
            detail={"role": role, "email": em},
        )
        out["user"] = u
        out["membership"] = {"user_id": uid, "tenant_id": tenant_id, "role": role}

    store.mutate(_fn)
    return out


def create_dataset(
    store: JsonStore,
    tenant_id: int,
    *,
    title: str,
    description: str,
    legal_basis_type: str,
    contains_personal_data: bool,
    confidentiality_note: str,
    visibility: str,
    actor: str,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        t = find_tenant(state, tenant_id)
        if not t or t["status"] != "active":
            raise HTTPException(status_code=400, detail="active tenant 만 데이터셋 등록 가능")
        did = next_id(state, "dataset")
        row = {
            "id": did,
            "tenant_id": tenant_id,
            "title": title.strip(),
            "description": description.strip(),
            "legal_basis_type": legal_basis_type,
            "contains_personal_data": bool(contains_personal_data),
            "confidentiality_note": confidentiality_note.strip(),
            "visibility": visibility,
            "status": "draft",
            "created_at": now_iso(),
        }
        state["datasets"].append(row)
        append_audit(
            state,
            tenant_id=tenant_id,
            actor=actor,
            action="dataset.create",
            resource_type="dataset",
            resource_id=str(did),
            detail={"title": title},
        )
        out.clear()
        out.update(row)

    store.mutate(_fn)
    return out


def publish_dataset(store: JsonStore, dataset_id: int, *, actor: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        d = find_dataset(state, dataset_id)
        if not d:
            raise HTTPException(status_code=404, detail="dataset 없음")
        if d["status"] != "draft":
            raise HTTPException(status_code=400, detail="draft 만 게시 가능")
        t = find_tenant(state, int(d["tenant_id"]))
        if not t or t["status"] != "active":
            raise HTTPException(status_code=400, detail="tenant 가 active 가 아님")
        d["status"] = "published"
        append_audit(
            state,
            tenant_id=int(d["tenant_id"]),
            actor=actor,
            action="dataset.publish",
            resource_type="dataset",
            resource_id=str(dataset_id),
            detail={},
        )
        out.clear()
        out.update(d)

    store.mutate(_fn)
    return out


def list_catalog(store: JsonStore, q: Optional[str]) -> List[Dict[str, Any]]:
    state = store.read()
    qn = (q or "").strip().lower()
    rows = [d for d in state["datasets"] if d["status"] == "published"]
    if qn:
        rows = [d for d in rows if qn in d["title"].lower()]
    return list(rows)


_MAX_BYTES = 25 * 1024 * 1024


def save_upload(
    store: JsonStore,
    tenant_id: int,
    *,
    original_name: str,
    content: bytes,
    dataset_id: Optional[int],
    actor: str,
) -> Dict[str, Any]:
    from pathlib import PurePath

    from .data_files import uploads_root

    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다 (최대 25MB)")

    safe = PurePath(original_name).name
    if not safe:
        raise HTTPException(status_code=400, detail="파일 이름이 비었습니다")

    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        t = find_tenant(state, tenant_id)
        if not t or t["status"] != "active":
            raise HTTPException(status_code=400, detail="active 기업만 업로드 가능")
        if dataset_id is not None:
            d = find_dataset(state, dataset_id)
            if not d or int(d["tenant_id"]) != tenant_id:
                raise HTTPException(status_code=404, detail="dataset 없음 또는 다른 기업 소속")

        uid = next_id(state, "upload")
        tenant_dir = uploads_root() / str(tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        dest = tenant_dir / f"{uid}_{safe}"
        dest.write_bytes(content)

        row = {
            "id": uid,
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
            "original_name": safe,
            "size_bytes": len(content),
            "stored_path": str(dest),
            "created_at": now_iso(),
        }
        state["uploads"].append(row)
        append_audit(
            state,
            tenant_id=tenant_id,
            actor=actor,
            action="file.upload",
            resource_type="upload",
            resource_id=str(uid),
            detail={"name": safe, "bytes": len(content)},
        )
        out.clear()
        out.update(row)

    store.mutate(_fn)
    return out


def list_uploads(store: JsonStore, tenant_id: int) -> List[Dict[str, Any]]:
    st = store.read()
    if not find_tenant(st, tenant_id):
        raise HTTPException(status_code=404, detail="tenant 없음")
    return [u for u in st["uploads"] if int(u["tenant_id"]) == tenant_id]


def save_connection_profile(
    store: JsonStore,
    tenant_id: int,
    *,
    label: str,
    engine: str,
    host: str,
    port: Optional[int],
    database: str,
    username: str,
    notes: str,
    actor: str,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    def _fn(state: Dict[str, Any]) -> None:
        t = find_tenant(state, tenant_id)
        if not t or t["status"] != "active":
            raise HTTPException(status_code=400, detail="active 기업만 연결 정보 등록 가능")
        cid = next_id(state, "connection")
        row = {
            "id": cid,
            "tenant_id": tenant_id,
            "label": label.strip(),
            "engine": engine,
            "host": host.strip(),
            "port": port,
            "database": database.strip(),
            "username": username.strip(),
            "notes": notes.strip(),
            "created_at": now_iso(),
        }
        state["connection_profiles"].append(row)
        append_audit(
            state,
            tenant_id=tenant_id,
            actor=actor,
            action="connection.create",
            resource_type="connection",
            resource_id=str(cid),
            detail={"engine": engine, "label": label},
        )
        out.clear()
        out.update(row)

    store.mutate(_fn)
    return out


def list_connections(store: JsonStore, tenant_id: int) -> List[Dict[str, Any]]:
    st = store.read()
    if not find_tenant(st, tenant_id):
        raise HTTPException(status_code=404, detail="tenant 없음")
    return [c for c in st["connection_profiles"] if int(c["tenant_id"]) == tenant_id]
