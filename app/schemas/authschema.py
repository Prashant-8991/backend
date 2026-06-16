from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class GoogleLoginRequest(BaseModel):
    id_token: str


class AuthUser(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_verified: bool
    is_active: bool
    picture: Optional[str] = None
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user: AuthUser
    access_token: str
    token_type: str = "bearer"


class PendingApprovalResponse(BaseModel):
    status: str = "pending_approval"
    message: str = "Your account is pending admin approval. Please wait for an administrator to approve your account."
    email: str
    full_name: str


class UserUpdateRole(BaseModel):
    role: str


class UserUpdateApproval(BaseModel):
    is_verified: bool


class UserListResponse(BaseModel):
    id: str
    email: str
    full_name: str
    picture: Optional[str] = None
    role: str
    is_verified: bool
    is_active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True
