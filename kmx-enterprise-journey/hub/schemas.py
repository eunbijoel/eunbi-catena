"""요청/응답 모양만 정의 (FastAPI 가 자동으로 검증·문서화)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterTenantIn(BaseModel):
    legal_name: str = Field(..., min_length=1, description="법인/사업장 이름")
    business_reg_no: str = Field(..., min_length=1, description="사업자등록번호 등")
    contact_email: EmailStr = Field(..., description="담당자 이메일")


class ApproveTenantIn(BaseModel):
    bpn: Optional[str] = Field(None, description="승인 시 발급하는 BPN (없으면 null)")


class AddUserIn(BaseModel):
    email: EmailStr
    display_name: str = ""
    role: Literal["org_admin", "data_steward", "security_auditor"] = "data_steward"


class DatasetCreateIn(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    legal_basis_type: Literal["none", "consent", "contract", "law"] = "none"
    contains_personal_data: bool = False
    confidentiality_note: str = Field("", description="한 줄 메모 (MVP 에선 빈 문자열도 허용)")
    visibility: Literal["private", "federation"] = "private"


class TenantOut(BaseModel):
    id: int
    legal_name: str
    business_reg_no: str
    contact_email: str
    status: str
    bpn: Optional[str]
    created_at: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: str


class MembershipOut(BaseModel):
    user_id: int
    tenant_id: int
    role: str


class DatasetOut(BaseModel):
    id: int
    tenant_id: int
    title: str
    description: str
    legal_basis_type: str
    contains_personal_data: bool
    confidentiality_note: str
    visibility: str
    status: str
    created_at: str


class AuditOut(BaseModel):
    id: int
    tenant_id: Optional[int]
    actor: str
    action: str
    resource_type: str
    resource_id: str
    detail: dict
    created_at: str


class ConnectionProfileIn(BaseModel):
    """비밀번호는 서버에 저장하지 않습니다(MVP). 나중에 Vault·커넥터로 이전."""

    label: str = Field(..., min_length=1, description="이 연결의 이름")
    engine: Literal["postgresql", "mysql", "mssql", "sqlite_file"] = "postgresql"
    host: str = ""
    port: Optional[int] = Field(None, ge=1, le=65535)
    database: str = ""
    username: str = ""
    notes: str = ""


class ConnectionProfileOut(BaseModel):
    id: int
    tenant_id: int
    label: str
    engine: str
    host: str
    port: Optional[int]
    database: str
    username: str
    notes: str
    created_at: str


class FileUploadOut(BaseModel):
    id: int
    tenant_id: int
    dataset_id: Optional[int]
    original_name: str
    size_bytes: int
    stored_path: str
    created_at: str
