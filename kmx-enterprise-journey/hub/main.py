"""
실행: 프로젝트 루트에서

    pip install -r requirements.txt
    uvicorn hub.main:app --reload --port 8090

브라우저: http://127.0.0.1:8090/app/ (시작은 / 안내 화면). 실행: 프로젝트 루트에서 ``python run_hub.py``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import logic
from .schemas import (
    AddUserIn,
    ApproveTenantIn,
    AuditOut,
    ConnectionProfileIn,
    ConnectionProfileOut,
    DatasetCreateIn,
    DatasetOut,
    FileUploadOut,
    MembershipOut,
    RegisterTenantIn,
    TenantOut,
    UserOut,
)
from .store import JsonStore, find_dataset, find_tenant

# Swagger /docs 맨 위 (짧게만)
_DOCS_INTRO = """
**[셀프서비스](/app/index.html)** — 일반 사용. **`/lab`** — API 시험 링크만 모은 페이지.

아래는 OpenAPI(Swagger) 화면입니다.
"""

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """터미널에 접속 주소를 한 번 찍어 줌."""
    print("\n[KMX Hub] 시작 페이지: http://127.0.0.1:8090/app/index.html  (다른 기기면 --host 0.0.0.0)\n")
    yield


_TAGS = [
    {
        "name": "0-안내",
        "description": "사용자용은 /app/index.html 부터.",
    },
    {
        "name": "1-기업 가입·승인",
        "description": "가입 요청(pending) → 허브가 승인(active) + BPN.",
    },
    {
        "name": "2-사용자",
        "description": "승인된 기업에 담당자 계정 붙이기.",
    },
    {
        "name": "3-데이터셋·카탈로그",
        "description": "제공자가 데이터 등록 → 게시하면 카탈로그에 노출.",
    },
    {
        "name": "4-감사",
        "description": "누가 무엇을 했는지 기록 조회.",
    },
    {
        "name": "5-데이터 원천",
        "description": "파일 업로드·DB 연결 프로필(비밀번호 미저장). 실제 쿼리는 다음 단계에서 커넥터로.",
    },
]

app = FastAPI(
    title="KMX Hub MVP",
    description=_DOCS_INTRO.strip(),
    version="0.1.0",
    openapi_tags=_TAGS,
    lifespan=_lifespan,
)

# import 시점보다 나중에 KMX_DATA_DIR 을 넣어도 되게, 첫 요청 때 한 번만 만듦
_hub_store: JsonStore | None = None


def get_store() -> JsonStore:
    global _hub_store
    if _hub_store is None:
        _hub_store = JsonStore()
    return _hub_store


def actor_email(x_actor: Optional[str] = None) -> str:
    """허브 운영자/시스템이 누가 행동했는지 감사에 남기기 위한 값. MVP 는 헤더로만 받음."""
    if x_actor and x_actor.strip():
        return x_actor.strip()
    return "anonymous"


@app.get("/", include_in_schema=False)
def root_to_app_index():
    """주소창에 서버 루트만 쳐도 시작 페이지로."""
    return RedirectResponse("/app/index.html", status_code=302)


@app.get(
    "/lab",
    response_class=HTMLResponse,
    tags=["0-안내"],
    summary="API 시험 안내",
    description="JSON 으로 직접 호출해 볼 때.",
)
def lab_page():
    return """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>API 시험</title></head>
<body style="font-family:system-ui;max-width:520px;margin:2rem auto;padding:0 1rem">
  <h1>API 시험대</h1>
  <p>여기는 개발·연동 확인용입니다. 입력칸 위주 화면은 <a href="/app/index.html">/app/index.html</a> 입니다.</p>
  <ul>
    <li><a href="/docs">Swagger (/docs)</a></li>
    <li><a href="/redoc">ReDoc</a></li>
  </ul>
</body></html>"""


# ----- 테넌트 -----


@app.post(
    "/api/tenants/register",
    response_model=TenantOut,
    tags=["1-기업 가입·승인"],
    summary="① 기업 가입 요청 (pending)",
    description="법인명·사업자번호·담당 이메일을 넣고 실행. 응답의 **id**를 다음 단계에 씁니다.",
)
def api_register_tenant(body: RegisterTenantIn):
    """G-1~G-2: 가입 요청 → 상태 pending."""
    row = logic.register_tenant(
        get_store(),
        legal_name=body.legal_name,
        business_reg_no=body.business_reg_no,
        contact_email=str(body.contact_email),
    )
    return TenantOut(**row)


@app.get(
    "/api/tenants",
    response_model=List[TenantOut],
    tags=["1-기업 가입·승인"],
    summary="등록된 기업 목록",
    description="방금 만든 tenant id 를 잊었을 때 여기서 확인.",
)
def api_list_tenants():
    """운영자가 목록 볼 때 (인증 없음 MVP)."""
    st = get_store().read()
    return [TenantOut(**t) for t in st["tenants"]]


@app.get(
    "/api/tenants/{tenant_id}",
    response_model=TenantOut,
    tags=["1-기업 가입·승인"],
    summary="기업 한 건 조회",
)
def api_get_tenant(tenant_id: int):
    st = get_store().read()
    t = find_tenant(st, tenant_id)
    if not t:
        raise HTTPException(404, "tenant 없음")
    return TenantOut(**t)


@app.post(
    "/api/tenants/{tenant_id}/approve",
    response_model=TenantOut,
    tags=["1-기업 가입·승인"],
    summary="② 허브가 기업 승인 (active + BPN)",
    description="`pending` 일 때만 가능. 헤더 X-Actor-Email 에 운영자 메일 넣으면 감사에 남습니다.",
)
def api_approve_tenant(tenant_id: int, body: ApproveTenantIn, x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email")):
    """G-3: pending → active, BPN 선택 입력."""
    row = logic.approve_tenant(get_store(), tenant_id, bpn=body.bpn, actor=actor_email(x_actor_email))
    return TenantOut(**row)


# ----- 사용자 / 멤버십 -----


@app.post(
    "/api/tenants/{tenant_id}/users",
    response_model=dict,
    tags=["2-사용자"],
    summary="③ 기업에 담당자 추가",
    description="`active` 된 tenant 만 가능. role 예: org_admin",
)
def api_add_user(tenant_id: int, body: AddUserIn, x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email")):
    """G-4: active tenant 에 사용자 + 역할."""
    r = logic.add_user_to_tenant(
        get_store(),
        tenant_id,
        email=str(body.email),
        display_name=body.display_name,
        role=body.role,
        actor=actor_email(x_actor_email),
    )
    return {"user": UserOut(**r["user"]), "membership": MembershipOut(**r["membership"])}


@app.get(
    "/api/tenants/{tenant_id}/users",
    response_model=List[dict],
    tags=["2-사용자"],
    summary="기업 소속 사용자 목록",
)
def api_list_tenant_users(tenant_id: int):
    st = get_store().read()
    if not find_tenant(st, tenant_id):
        raise HTTPException(404, "tenant 없음")
    uids = {int(m["user_id"]) for m in st["memberships"] if int(m["tenant_id"]) == tenant_id}
    users = {int(u["id"]): u for u in st["users"]}
    out = []
    for m in st["memberships"]:
        if int(m["tenant_id"]) != tenant_id:
            continue
        u = users.get(int(m["user_id"]))
        if u:
            out.append({"user": UserOut(**u), "membership": MembershipOut(**m)})
    return out


# ----- 데이터셋 (제공자) -----


@app.post(
    "/api/tenants/{tenant_id}/datasets",
    response_model=DatasetOut,
    tags=["3-데이터셋·카탈로그"],
    summary="④ 데이터셋 초안 만들기 (draft)",
    description="법적 메타는 베이직 필드만. 응답의 **id** = dataset_id 로 publish 에 사용.",
)
def api_create_dataset(tenant_id: int, body: DatasetCreateIn, x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email")):
    row = logic.create_dataset(
        get_store(),
        tenant_id,
        title=body.title,
        description=body.description,
        legal_basis_type=body.legal_basis_type,
        contains_personal_data=body.contains_personal_data,
        confidentiality_note=body.confidentiality_note,
        visibility=body.visibility,
        actor=actor_email(x_actor_email),
    )
    return DatasetOut(**row)


@app.get(
    "/api/tenants/{tenant_id}/datasets",
    response_model=List[DatasetOut],
    tags=["3-데이터셋·카탈로그"],
    summary="그 기업 데이터셋 전부",
)
def api_list_tenant_datasets(tenant_id: int):
    st = get_store().read()
    if not find_tenant(st, tenant_id):
        raise HTTPException(404, "tenant 없음")
    rows = [d for d in st["datasets"] if int(d["tenant_id"]) == tenant_id]
    return [DatasetOut(**d) for d in rows]


@app.get(
    "/api/datasets/{dataset_id}",
    response_model=DatasetOut,
    tags=["3-데이터셋·카탈로그"],
    summary="데이터셋 한 건",
)
def api_get_dataset(dataset_id: int):
    st = get_store().read()
    d = find_dataset(st, dataset_id)
    if not d:
        raise HTTPException(404, "dataset 없음")
    return DatasetOut(**d)


@app.post(
    "/api/datasets/{dataset_id}/publish",
    response_model=DatasetOut,
    tags=["3-데이터셋·카탈로그"],
    summary="⑤ 카탈로그에 게시 (published)",
    description="draft 만 가능. 이후 GET /api/catalog/datasets 에 보입니다.",
)
def api_publish_dataset(dataset_id: int, x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email")):
    """게시 → 카탈로그 API 에 노출. (DSP/EDC 연동은 여기 이후 훅으로 붙이면 됨)"""
    row = logic.publish_dataset(get_store(), dataset_id, actor=actor_email(x_actor_email))
    return DatasetOut(**row)


# ----- 카탈로그 -----


@app.get(
    "/api/catalog/datasets",
    response_model=List[DatasetOut],
    tags=["3-데이터셋·카탈로그"],
    summary="⑥ 밖에서 보는 카탈로그 (published 만)",
)
def api_catalog(q: Optional[str] = Query(None, description="제목 부분 검색")):
    """published 만."""
    rows = logic.list_catalog(get_store(), q)
    return [DatasetOut(**d) for d in rows]


# ----- 감사 -----


@app.get(
    "/api/audit",
    response_model=List[AuditOut],
    tags=["4-감사"],
    summary="⑦ 감사 로그",
    description="최근 이벤트부터. tenant_id 쿼리로 한 기업만 필터 가능.",
)
def api_audit(tenant_id: Optional[int] = None, limit: int = Query(50, ge=1, le=200)):
    st = get_store().read()
    ev = list(reversed(st["audit_events"]))
    if tenant_id is not None:
        ev = [e for e in ev if e.get("tenant_id") == tenant_id]
    return [AuditOut(**e) for e in ev[:limit]]


# ----- 파일 · DB 연결 프로필 (원천 데이터) -----


@app.post(
    "/api/tenants/{tenant_id}/files",
    response_model=FileUploadOut,
    tags=["5-데이터 원천"],
    summary="파일 업로드",
    description="실제 파일을 `data/uploads/{기업번호}/` 에 저장하고 메타만 JSON 에 남깁니다.",
)
async def api_upload_file(
    tenant_id: int,
    file: UploadFile = File(...),
    dataset_id: Optional[int] = Form(None),
    x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email"),
):
    raw = await file.read()
    row = logic.save_upload(
        get_store(),
        tenant_id,
        original_name=file.filename or "upload.bin",
        content=raw,
        dataset_id=dataset_id,
        actor=actor_email(x_actor_email),
    )
    return FileUploadOut(**row)


@app.get(
    "/api/tenants/{tenant_id}/files",
    response_model=List[FileUploadOut],
    tags=["5-데이터 원천"],
    summary="업로드한 파일 목록",
)
def api_list_files(tenant_id: int):
    rows = logic.list_uploads(get_store(), tenant_id)
    return [FileUploadOut(**r) for r in rows]


@app.post(
    "/api/tenants/{tenant_id}/connections",
    response_model=ConnectionProfileOut,
    tags=["5-데이터 원천"],
    summary="DB 연결 프로필 저장",
    description="접속 정보(호스트·DB명 등)만 저장합니다. **비밀번호는 저장하지 않습니다** — 이후 커넥터·Vault 로 이어가면 됩니다.",
)
def api_create_connection(
    tenant_id: int,
    body: ConnectionProfileIn,
    x_actor_email: Optional[str] = Header(None, alias="X-Actor-Email"),
):
    row = logic.save_connection_profile(
        get_store(),
        tenant_id,
        label=body.label,
        engine=body.engine,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        notes=body.notes,
        actor=actor_email(x_actor_email),
    )
    return ConnectionProfileOut(**row)


@app.get(
    "/api/tenants/{tenant_id}/connections",
    response_model=List[ConnectionProfileOut],
    tags=["5-데이터 원천"],
    summary="DB 연결 프로필 목록",
)
def api_list_connections(tenant_id: int):
    rows = logic.list_connections(get_store(), tenant_id)
    return [ConnectionProfileOut(**r) for r in rows]


# /app , /app/ → 시작 URL 을 주소창에 고정 (/app/index.html)
@app.get("/app", include_in_schema=False)
def redirect_app_noslash():
    return RedirectResponse("/app/index.html", status_code=302)


@app.get("/app/", include_in_schema=False)
def redirect_app_slash():
    return RedirectResponse("/app/index.html", status_code=302)


# 정적 페이지 (마지막에 등록: /app/* )
_APP_DIR = Path(__file__).resolve().parent / "static" / "app"
app.mount("/app", StaticFiles(directory=str(_APP_DIR), html=True), name="app_pages")
