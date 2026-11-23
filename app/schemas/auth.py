from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, description="Email or Username")
    password: str = Field(..., min_length=6)
    device_id: str = Field(default="default-device")
    platform: str = Field(default="web", description="web, android, ios")
    device_name: Optional[str] = Field(default="Web Browser")


class UserResponse(BaseModel):
    id: str
    full_name: str
    email: str
    mobile: Optional[str] = None
    role: str
    account_status: str
    password_reset_requested: Optional[bool] = False
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AdminPasswordResetRequestBody(BaseModel):
    email: str = Field(..., min_length=3, description="Registered email or username")
    reason: Optional[str] = None
    device_model: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserResponse


class RefreshTokenRequest(BaseModel):
    refresh_token: str
    device_id: str


class InviteUserRequest(BaseModel):
    full_name: str = Field(..., min_length=2)
    email: EmailStr
    mobile: str = Field(..., min_length=8)
    role: str = Field(..., description="admin or executive")


class InviteUserResponse(BaseModel):
    message: str
    user_id: str
    email: str
    role: str
    delivery_status: str
    provider_message_id: Optional[str] = None


class ValidateTokenResponse(BaseModel):
    valid: bool
    email: str
    full_name: str
    role: str


class ActivateAccountRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, description="Minimum 8 characters")


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class DirectUserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2)
    email: EmailStr
    password: str = Field(..., min_length=6, description="Initial login password")
    role: str = Field(..., description="admin or executive")
    mobile: Optional[str] = Field(None, min_length=8)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, description="New login password set by Super Admin")


class SessionResponse(BaseModel):
    id: str
    device_id: str
    platform: str
    device_name: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime
    last_activity_at: datetime
    is_current: bool = False
    status: str


class PasswordResetQueueItem(BaseModel):
    id: str
    req_id: str
    user_id: str
    email: str
    full_name: str
    mobile: Optional[str] = None
    role: str
    cluster: Optional[str] = "Main Operations"
    reporting_manager: Optional[str] = "Operations Admin"
    reason: str
    device_signature: Optional[str] = "Mobile App"
    client_ip: Optional[str] = "Origin IP"
    status: str  # "pending" | "approved" | "rejected"
    priority: str  # "High SLA Priority" | "Standard SLA"
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_action: Optional[str] = None


class PasswordResetQueueResponse(BaseModel):
    items: List[PasswordResetQueueItem]
    pending_count: int
    approved_count: int
    rejected_count: int
    recent_dispatches: List[dict]


class AuthorizeResetRequest(BaseModel):
    new_password: str = Field(..., min_length=6, description="Cryptographic temporary or new master password")


class RejectResetRequest(BaseModel):
    reason: Optional[str] = Field("Declined by Super Admin - Unmatched origin", description="Rejection reason")

