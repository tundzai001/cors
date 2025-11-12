# backend/app/schemas.py
from pydantic import BaseModel, Field, validator
from typing import Any, List

# --- DEVICE SCHEMAS ---
class DeviceBase(BaseModel):
    serial: str
    name: str | None = None
    status: str | None = "offline"
    timestamp: int | None = 0
    
    class Config:
        from_attributes = True

class Device(DeviceBase):
    bps: int | None = 0
    detected_chip_type: str | None = "UNKNOWN"
    
    base_config: dict | None = Field(default_factory=dict)
    service_config: dict | None = Field(default_factory=dict)
    
    # ✅ THAY ĐỔI: Chấp nhận cả `bool` và `None`, với giá trị mặc định là False
    ntrip_connected: bool | None = False
    ntrip_status: dict | None = Field(default_factory=dict)
    is_locked: bool | None = False

class Command(BaseModel):
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)

class ServiceConfig(BaseModel):
    ncomport: str | None = Field(None, description="Định danh trạm hoặc ID cổng COM")
    reconnectioninterval: int | None = 10
    server1_enabled: bool = False
    serverhost1: str | None = None
    port1: int | None = None
    mountpoint1: str | None = None
    password1: str | None = None
    server2_enabled: bool = False
    serverhost2: str | None = None
    port2: int | None = None
    mountpoint2: str | None = None
    password2: str | None = None
    rtcm_enabled: bool = False
    rtcmserver1: str | None = None
    rtcmport1: int | None = None
    rtcmmountpoint1: str | None = None
    rtcmusername1: str | None = None
    rtcmpassword1: str | None = None

class LicenseRequest(BaseModel):
    serial: str

class LicenseResponse(BaseModel):
    serial: str
    license_key: str

# --- USER SCHEMAS ---
class UserBase(BaseModel):
    username: str
    full_name: str | None = None
    role: str
    
    @validator('role')
    def validate_role(cls, v):
        allowed_roles = ['admin', 'viewer', 'coordinator']
        if v not in allowed_roles:
            raise ValueError(f'Role phải là một trong: {allowed_roles}')
        return v

class UserCreate(UserBase):
    password: str
    assigned_devices: List[str] | None = Field(default_factory=list)
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Mật khẩu phải có ít nhất 6 ký tự')
        return v

class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None
    assigned_devices: List[str] | None = None
    @validator('role')
    def validate_role(cls, v):
        if v is not None:
            allowed_roles = ['admin', 'viewer', 'coordinator']
            if v not in allowed_roles:
                raise ValueError(f'Role phải là một trong: {allowed_roles}')
        return v
    
    @validator('password')
    def validate_password(cls, v):
        if v is not None and len(v) < 6:
            raise ValueError('Mật khẩu phải có ít nhất 6 ký tự')
        return v

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: int
    permissions: list[str] = []
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class LoginRequest(BaseModel):
    username: str
    password: str