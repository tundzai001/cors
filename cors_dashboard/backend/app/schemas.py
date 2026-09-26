# backend/app/schemas.py
from pydantic import BaseModel, Field, validator
from typing import Any, List, Optional


class ZaloBindingAdminUpdate(BaseModel):
    display_name: Optional[str] = None
    binding_type: Optional[str] = None
    is_active: Optional[bool] = None
    station_scope_mode: Optional[str] = None
    allowed_bases: Optional[List[str]] = None
    notifications_enabled: Optional[bool] = None
    min_severity: Optional[str] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    critical_override: Optional[bool] = None

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

    @validator("system_info", pre=True)
    def ensure_dict_system(cls, v):
        # 🔥 FIX: Nếu DB chứa list [] thay vì dict {}, tự động chuyển về {}
        if isinstance(v, list):
            return {}
        return v or {}

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
    sync_errors: list[str] | None = Field(default_factory=list)

    @validator("base_config", "service_config", "ntrip_status", pre=True)
    def ensure_dict_device(cls, v):
        # 🔥 FIX: Nếu DB chứa list [] thay vì dict {}, tự động chuyển về {}
        if isinstance(v, list):
            return {}
        return v or {}

class Command(BaseModel):
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfiguredRelayPowerRequest(BaseModel):
    active: bool


class BaseReferenceCheckRequest(BaseModel):
    ntrip_host: str = Field(..., min_length=1, max_length=255)
    ntrip_port: int = Field(..., ge=1, le=65535)
    ntrip_username: str = Field(..., min_length=1, max_length=128)
    ntrip_password: str = Field(..., min_length=1, max_length=256)
    mountpoint: str = Field(..., min_length=1, max_length=128)
    reference_lat: float = Field(..., ge=-90, le=90)
    reference_lon: float = Field(..., ge=-180, le=180)
    reference_alt: float = Field(..., ge=-1000, le=100000)
    timeout: int = Field(300, ge=30, le=3600)
    samples: int = Field(60, ge=15, le=300)


class CasterMountpointLookupRequest(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class DeviceRenameSerial(BaseModel):
    new_serial: str

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



class LicenseRequest(BaseModel):
    serial: str

class LicenseResponse(BaseModel):
    serial: str
    license_key: str


class CasterSourceCreateRequest(BaseModel):
    host: Optional[str] = None
    identifier: str
    mountpoint: str
    password: str = "12345"
    counter: int = 0
    index: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    auth_required: bool = True
    gga_required: bool = False
    show_position: bool = False
    overlap_data: bool = False
    re_positioning: bool = False
    storing_data: bool = False


class CasterSourceDeleteRequest(BaseModel):
    host: Optional[str] = None
    identifier: str
    mountpoint: str

# --- USER SCHEMAS ---
class UserBase(BaseModel):
    username: str
    full_name: str | None = None
    role: str
    is_active: bool = True
    
    @validator('role')
    def validate_role(cls, v):
        allowed_roles = ['admin', 'viewer', 'coordinator']
        if v not in allowed_roles:
            raise ValueError(f'Role phải là một trong: {allowed_roles}')
        return v

class UserCreate(UserBase):
    password: str
    assigned_devices: List[str] | None = Field(default_factory=list)
    can_view_rovers: bool = False
    can_create_rovers: bool = False
    can_create_users: bool = False
    extra_mountpoints: str | None = None
    allowed_bases: List[str] | None = None
    view_only_bases: List[str] | None = None
    allowed_rovers: List[str] | None = None
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
    can_view_rovers: bool | None = None
    can_create_rovers: bool | None = None
    can_create_users: bool | None = None
    extra_mountpoints: str | None = None
    allowed_bases: List[str] | None = None
    view_only_bases: List[str] | None = None
    allowed_rovers: List[str] | None = None
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
    created_at: int
    permissions: list[str] = []
    can_view_rovers: bool = False
    can_create_rovers: bool = False
    can_create_users: bool = False
    extra_mountpoints: str | None = None
    parent_id: int | None = None
    allowed_bases: List[str] | None = None
    view_only_bases: List[str] | None = None
    allowed_rovers: List[str] | None = None
    app_version: str | None = None
    
    @validator("allowed_bases", "view_only_bases", "allowed_rovers", pre=True)
    def parse_json_lists(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return []
        return v

    class Config:
        from_attributes = True

class RoverAccountBase(BaseModel):
    username: str
    password: str

class RoverAccountCreate(RoverAccountBase):
    mountpoint_allow: str | None = None
    expiry_days: int = 365
    show_position: bool = True
    rtcm_cycle_enabled: bool = False
    rtcm_disconnect_interval_seconds: int = 300  # Default 5 minutes (300 seconds)
    rtcm_disconnect_duration_seconds: int = 10
    is_shared_with_managers: bool = False

class RoverAccountResponse(RoverAccountBase):
    id: int
    base_serial: str
    is_active: bool
    created_at: int
    base_name: str | None = None
    mountpoints: str | None = None 
    max_connections: int = 1
    show_position: bool = True
    target_casters: str | None = None
    rtcm_cycle_enabled: bool = False
    rtcm_disconnect_interval_seconds: int = 300
    rtcm_disconnect_duration_seconds: int = 10
    expires_at: Optional[int] = None
    is_shared_with_managers: bool = False
    auto_save_fixed_points: bool = False
    
    class Config: from_attributes = True

class RoverMultiCreate(BaseModel):
    username: str
    password: str
    expiry_days: int = 365
    selected_serials: List[str] # Danh sách serial các trạm đã chọn
    mountpoint_override: str    # Chuỗi Mountpoint (người dùng chốt)
    target_casters: Optional[List[str]] = Field(default_factory=list)
    max_connections: int = 1
    show_position: bool = True
    rtcm_cycle_enabled: bool = False
    rtcm_disconnect_interval_seconds: int = 300
    rtcm_disconnect_duration_seconds: int = 10
    is_shared_with_managers: bool = False
    
class RoverUpdate(BaseModel):
    password: str | None = None
    mountpoints: str | None = None # Cho phép sửa chuỗi mountpoint
    expiry_days: int | None = None # Cho phép gia hạn lại
    max_connections: int | None = None
    show_position: bool | None = None
    rtcm_cycle_enabled: bool | None = None
    rtcm_disconnect_interval_seconds: int | None = None
    rtcm_disconnect_duration_seconds: int | None = None
    is_shared_with_managers: bool | None = None
    auto_save_fixed_points: bool | None = None
    selected_serials: list[str] | None = None # For syncing unknown users
    target_casters: list[str] | None = None
    
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False

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


# --- OPERATIONS / BULK ACTION SCHEMAS ---
class RoverBulkRenewRequest(BaseModel):
    usernames: List[str] = Field(default_factory=list)
    expiry_days: int = Field(365, ge=1, le=3650)
    sync_caster: bool = False


class RoverSyncStatusRequest(BaseModel):
    usernames: List[str] | None = None


class DeviceBulkActionRequest(BaseModel):
    serials: List[str] = Field(default_factory=list)
    action: str


class DeviceConfigTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str | None = None
    base_config: dict | None = None
    service_config: dict | None = None


class DeviceApplyTemplateRequest(BaseModel):
    template_id: str
    serials: List[str] = Field(default_factory=list)
    apply_base_config: bool = True
    apply_service_config: bool = True


class ZaloGroupLinkCodeCreate(BaseModel):
    binding_type: str
    station_scope_mode: str = "all"
    allowed_bases: List[str] = Field(default_factory=list)


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
    last_update: int = 0
    identifier: str = ""
    nclient: int = 0
    maxclient: int = 0
    in_db: bool = False  # Có trong DB local không
    is_blocked: bool = False  # Có trong blocklist không
    blocked_reason: Optional[str] = None
    blocked_at: Optional[int] = None
    server: Optional[str] = None
    version: Optional[str] = None
    active_connections: List[dict] = Field(default_factory=list) # List of {mountpoint, uptime}
    show_position: bool = True


class CasterUsersResponse(BaseModel):
    server_time: int = 0
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
