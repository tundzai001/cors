# backend/app/schemas.py
from pydantic import BaseModel, Field, validator
from typing import Any, List, Optional

# --- DEVICE SCHEMAS ---
class DeviceBase(BaseModel):
    serial: str
    name: str | None = None
    status: str | None = "offline"
    timestamp: int | None = 0
    version: str | None = None
    system_info: Optional[dict] = None
    
    class Config:
        from_attributes = True

class Device(DeviceBase):
    bps: int | None = 0
    detected_chip_type: str | None = "UNKNOWN"
    user_id: int | None = None  
    
    base_config: dict | None = Field(default_factory=dict)
    service_config: dict | None = Field(default_factory=dict)
    
    ntrip_connected: bool | None = False
    ntrip_status: dict | None = Field(default_factory=dict)
    is_locked: bool | None = False

    is_synced: bool | None = True

class Command(BaseModel):
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)

class ServiceConfig(BaseModel):
    ncomport: str | None = Field(None, description="Định danh trạm hoặc ID cổng COM")
    reconnectioninterval: int = 10
    stream_on_demand: bool = False
    stream_active: Optional[bool] = None
    server1_stream_on_demand: Optional[bool] = None
    server1_stream_active: Optional[bool] = None
    server2_stream_on_demand: Optional[bool] = None
    server2_stream_active: Optional[bool] = None
    
    # Server 1
    server1_enabled: bool = False
    ntrip_version1: int = 1
    username1: str | None = None      
    serverhost1: str | None = None
    port1: int | None = None
    mountpoint1: str | None = None
    password1: str | None = None
    
    # Server 2
    server2_enabled: bool = False
    ntrip_version2: int = 1
    username2: str | None = None     
    serverhost2: str | None = None
    port2: int | None = None
    mountpoint2: str | None = None
    password2: str | None = None
    
    # RTCM Client (giữ nguyên)
    rtcm_enabled: bool = False
    rtcmntripversion1: int = 1
    rtcmserver1: str | None = None
    rtcmport1: int | None = None
    rtcmmountpoint1: str | None = None
    rtcmusername1: str | None = None
    rtcmpassword1: str | None = None


class VirtualStationCreate(BaseModel):
    name: str
    lat: float
    lon: float
    alt: float = 0.0
    serial: str | None = None
    service_config: ServiceConfig = Field(default_factory=ServiceConfig)

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
        allowed_roles = ['admin', 'viewer', 'coordinator', 'operator']
        if v not in allowed_roles:
            raise ValueError(f'Role phải là một trong: {allowed_roles}')
        return v

class UserCreate(UserBase):
    password: str
    assigned_devices: List[str] | None = Field(default_factory=list)
    can_create_rovers: bool = False
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Mật khẩu phải có ít nhất 6 ký tự')
        return v

class DeviceRename(BaseModel):
    name: str
    
class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None
    assigned_devices: List[str] | None = None
    can_create_rovers: bool | None = None
    @validator('role')
    def validate_role(cls, v):
        if v is not None:
            allowed_roles = ['admin', 'viewer', 'coordinator', 'operator']
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
    can_create_rovers: bool = False
    app_version: str | None = None
    
    class Config:
        from_attributes = True

class RoverAccountBase(BaseModel):
    username: str
    password: str

class RoverAccountCreate(RoverAccountBase):
    mountpoint_allow: str | None = None
    expiry_days: int = 365

class RoverAccountResponse(RoverAccountBase):
    id: int
    base_serial: str
    is_active: bool
    created_at: int
    base_name: str | None = None
    mountpoints: str | None = None 
    
    class Config: from_attributes = True

class RoverMultiCreate(BaseModel):
    username: str
    password: str
    expiry_days: int = 365
    selected_serials: List[str] # Danh sách serial các trạm đã chọn
    mountpoint_override: str    # Chuỗi Mountpoint (người dùng chốt)
    target_casters: Optional[List[str]] = Field(default_factory=list) # Danh sách Host muốn add user
    
class RoverUpdate(BaseModel):
    password: str | None = None
    mountpoints: str | None = None # Cho phép sửa chuỗi mountpoint
    expiry_days: int | None = None # Cho phép gia hạn lại
    
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class LoginRequest(BaseModel):
    username: str
    password: str

class CasterConfig(BaseModel):
    host: str
    port: int


class RTCMStationActivateRequest(BaseModel):
    client_instance_id: Optional[str] = None
    ttl_seconds: int = 90
    mountpoint: Optional[str] = None
    caster_host: Optional[str] = None


class RTCMNtripStartRequest(BaseModel):
    serial: str
    mountpoint: str
    client_instance_id: str
    ttl_seconds: int = 90


class RTCMNtripStopRequest(BaseModel):
    serial: str
    client_instance_id: str


class RTCMSessionHeartbeatRequest(BaseModel):
    ttl_seconds: int = 90


class RTCMSessionResponse(BaseModel):
    session_id: str
    serial: str
    client_id: str
    expires_at: int
    caster_host: Optional[str] = None
    active_sessions_for_station: int
    stream_status: str
    connect_after_seconds: int = 3
    mountpoints: List[str] = Field(default_factory=list)
    selected_mountpoint: Optional[str] = None


class RTCMActiveStationResponse(BaseModel):
    serial: str
    active_sessions: int
    client_ids: List[str] = Field(default_factory=list)

# --- ACTIVITY LOG SCHEMAS ---
class ActivityLogBase(BaseModel):
    timestamp: int
    type: str
    message: str
    icon: str

class ActivityLogCreate(ActivityLogBase):
    user_id: Optional[int] = None
    device_serial: Optional[str] = None
    ip_address: Optional[str] = None

class ActivityLogResponse(ActivityLogBase):
    id: int
    class Config:
        from_attributes = True

class HeatmapData(BaseModel):
    # Trả về mảng 2 chiều: 7 ngày x 24 giờ
    data: List[List[int]]


# --- CASTER ROVER MANAGEMENT SCHEMAS ---
class BlockRoverRequest(BaseModel):
    username: str
    mountpoints: Optional[List[str]] = None # List các mountpoint cần chặn. Trống = chặn tất cả.
    reason: Optional[str] = None


class UnblockRoverRequest(BaseModel):
    username: str
    mountpoint: Optional[str] = None


class CasterRoverItem(BaseModel):
    username: str
    password: str
    mountpoints: List[str] = Field(default_factory=list)
    datacount: int = 0
    status: int = 0
    identifier: str = ""
    nclient: int = 0
    maxclient: int = 0
    in_db: bool = False  # Có trong DB local không
    is_blocked: bool = False  # Có trong blocklist không
    blocked_reason: Optional[str] = None
    blocked_at: Optional[int] = None
    server: Optional[str] = None
    version: Optional[str] = None


class CasterUsersResponse(BaseModel):
    total: int
    in_db_count: int
    unknown_count: int
    blocked_count: int
    items: List[CasterRoverItem]


class NtripAuthResponse(BaseModel):
    allowed: bool
    username: str
    mountpoint: Optional[str] = None
    reason: Optional[str] = None