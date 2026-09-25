# ==============================================================================
# == backend/app/models.py ==
# ==============================================================================

from sqlalchemy import Column, String, Integer, BigInteger, Boolean, JSON, Index, Text, ForeignKey, UniqueConstraint, Float, SmallInteger
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base, AuthBase 

class Device(Base):
    __tablename__ = "devices"

    serial = Column(String, primary_key=True, index=True)
    name = Column(String, index=True)
    status = Column(String, default="offline", index=True)
    device_token = Column(String, index=True, nullable=True)
    timestamp = Column(BigInteger, default=0, index=True)
    bps = Column(Integer, default=0)
    detected_chip_type = Column(String, default="UNKNOWN")
    user_id = Column(Integer, nullable=True, index=True)
    base_config = Column(JSONB, nullable=True)
    service_config = Column(JSONB, nullable=True)
    ntrip_connected = Column(Boolean, default=False)
    ntrip_status = Column(JSONB, nullable=True)
    is_locked = Column(Boolean, default=False)
    is_provisioned = Column(Boolean, default=False)
    system_info = Column(JSONB, nullable=True)
    rtk_fix_status = Column(String, default="NO_FIX")  
    base_mode_active = Column(Boolean, default=False)  
    version = Column(String, nullable=True)
    
    __table_args__ = (
        Index('ix_user_status', 'user_id', 'status'),
        Index('ix_status_timestamp', 'status', 'timestamp'),
    )
    is_synced = Column(Boolean, default=True, nullable=False)
class RoverAccount(AuthBase):
    __tablename__ = "rover_accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    base_serial = Column(String, index=True) 
    mountpoints = Column(String, nullable=True) 
    created_by_user_id = Column(Integer)
    is_active = Column(Boolean, default=True)
    max_connections = Column(Integer, default=1)
    show_position = Column(Boolean, default=True)
    target_casters = Column(String, nullable=True) # Semicolon separated hosts
    created_at = Column(Integer)
    rtcm_cycle_enabled = Column(Boolean, default=False)  # Enable periodic RTCM disconnect/reconnect
    rtcm_disconnect_interval_seconds = Column(Integer, default=300)  # Default 5 minutes
    rtcm_disconnect_duration_seconds = Column(Integer, default=10) # Pause duration
    expires_at = Column(BigInteger, nullable=True) # Unix timestamp of expiration, None = infinite
    is_shared_with_managers = Column(Boolean, default=False)
    auto_save_fixed_points = Column(Boolean, default=True, server_default="true", nullable=False)


class RoverSessionHistory(AuthBase):
    __tablename__ = "rover_session_history"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, index=True)
    server = Column(String, nullable=False, index=True)
    mountpoint = Column(String, nullable=False, default="")
    client_id = Column(String, nullable=False, default="")
    receiver_id = Column(String, nullable=False, default="")
    started_at = Column(BigInteger, nullable=True)
    last_seen_at = Column(BigInteger, nullable=False, index=True)
    ended_at = Column(BigInteger, nullable=True, index=True)
    uptime_seconds = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    position_fix_quality = Column(SmallInteger, nullable=True)
    position_updated_at = Column(BigInteger, nullable=True)
    position_satellite_count = Column(Integer, nullable=True)
    latest_fix_quality = Column(SmallInteger, nullable=True)
    satellite_count = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_rover_session_history_lookup", "username", "server", "last_seen_at"),
    )


class RoverFixedPoint(AuthBase):
    __tablename__ = "rover_fixed_points"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("rover_session_history.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(80), nullable=False, default="Điểm đo")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    fix_quality = Column(SmallInteger, nullable=False, default=4)
    satellite_count = Column(Integer, nullable=True)
    recorded_at = Column(BigInteger, nullable=False)
    saved_at = Column(BigInteger, nullable=False)
    created_by = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_rover_fixed_points_session_recorded", "session_id", "recorded_at"),
    )


class User(AuthBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="viewer", index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(BigInteger, nullable=False)
    can_view_rovers = Column(Boolean, default=False, nullable=False)  # Rover page/map access
    can_create_rovers = Column(Boolean, default=False, nullable=False)  # Create/edit access (legacy column name)
    can_create_users = Column(Boolean, default=False, nullable=False)
    extra_mountpoints = Column(String, nullable=True) # Danh sÃ¡ch MP bÃ´Ì‰ sung (dÃ¢Ì u ; ngÄƒn caÌ ch)
    parent_id = Column(Integer, index=True, nullable=True) # ID của coordinator đã tạo ra viewer này
    allowed_bases = Column(String, nullable=True) # JSON array các serial trạm Base
    view_only_bases = Column(String, nullable=True)  # Coordinator stations with read-only access
    allowed_rovers = Column(String, nullable=True) # JSON array các username của Rover

class IpSecurityLog(AuthBase):
    __tablename__ = 'ip_security_logs'
    
    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String, unique=True, index=True, nullable=False)
    block_type = Column(String, nullable=False)  # 'temporary' or 'permanent'
    blocked_until = Column(Integer, nullable=True)  # Unix timestamp, None for permanent
    created_at = Column(Integer, nullable=False)
    reason = Column(String, nullable=True, default="Rate limit exceeded")

class SystemSetting(AuthBase):
    __tablename__ = 'system_settings'
    
    key = Column(String, primary_key=True, index=True)
    value = Column(JSON, nullable=False)


class ZaloChatBinding(AuthBase):
    __tablename__ = "zalo_chat_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, unique=True, nullable=False, index=True)
    chat_type = Column(String, nullable=False, default="private")
    binding_type = Column(String, nullable=False, index=True)  # private, group_admin, group_public
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    sender_id = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    onboarding_completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=False)
    registered_by = Column(String, nullable=True)
    station_scope_mode = Column(String, nullable=False, default="all")
    allowed_bases = Column(JSON, nullable=False, default=list)


class ZaloLinkCode(AuthBase):
    __tablename__ = "zalo_link_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False, index=True)
    used_at = Column(BigInteger, nullable=True)
    used_by_chat_id = Column(String, nullable=True)
    link_type = Column(String, nullable=False, default="private", index=True)
    station_scope_mode = Column(String, nullable=False, default="all")
    allowed_bases = Column(JSON, nullable=False, default=list)


class ZaloWorkflowState(AuthBase):
    __tablename__ = "zalo_workflow_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    sender_id = Column(String, nullable=False)
    flow = Column(String, nullable=False, index=True)
    step = Column(String, nullable=False)
    state_encrypted = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False, index=True)

    __table_args__ = (UniqueConstraint("chat_id", "sender_id", name="uq_zalo_workflow_actor"),)


class ZaloPendingAction(AuthBase):
    __tablename__ = "zalo_pending_actions"

    id = Column(String, primary_key=True)
    code_hash = Column(String, unique=True, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    sender_id = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    risk_level = Column(String, nullable=False, default="standard")
    target_identifier = Column(String, nullable=True)
    payload_encrypted = Column(Text, nullable=False)
    preview = Column(Text, nullable=False)
    state_fingerprint = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    created_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False, index=True)
    executed_at = Column(BigInteger, nullable=True)


class ZaloNotificationPreference(AuthBase):
    __tablename__ = "zalo_notification_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    binding_id = Column(Integer, ForeignKey("zalo_chat_bindings.id", ondelete="CASCADE"), unique=True, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    min_severity = Column(String, nullable=False, default="critical")
    scopes = Column(JSON, nullable=False, default=dict)
    quiet_start = Column(String, nullable=True)
    quiet_end = Column(String, nullable=True)
    timezone = Column(String, nullable=False, default="Asia/Saigon")
    critical_override = Column(Boolean, nullable=False, default=True)
    updated_at = Column(BigInteger, nullable=False)


class ZaloDeliveryLog(AuthBase):
    __tablename__ = "zalo_delivery_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    binding_id = Column(Integer, ForeignKey("zalo_chat_bindings.id", ondelete="CASCADE"), nullable=False, index=True)
    event_key = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    sent_at = Column(BigInteger, nullable=True)

    __table_args__ = (UniqueConstraint("binding_id", "event_key", name="uq_zalo_delivery_event"),)

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(BigInteger, index=True, nullable=False) # Unix timestamp
    type = Column(String, index=True)  # success, error, warning, info
    message = Column(String)
    icon = Column(String, default="📝") # Icon hiển thị (✅, ❌, ⚠️...)
    
    # Metadata (tùy chọn để lọc sau này)
    user_id = Column(Integer, nullable=True)
    device_serial = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)


class RoverBlocklist(AuthBase):
    __tablename__ = "rover_blocklist"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True, nullable=False)
    mountpoint = Column(String, nullable=True)  # null = block all mountpoints
    blocked_at = Column(BigInteger, nullable=False)
    blocked_by = Column(String, nullable=True)  # admin username who blocked
    reason = Column(String, nullable=True)

    __table_args__ = (
        Index('ix_blocklist_user_mp', 'username', 'mountpoint'),
    )


class RTCMStreamSession(Base):
    __tablename__ = "rtcm_stream_sessions"

    session_id = Column(String, primary_key=True, index=True)
    serial = Column(String, nullable=False, index=True)
    mountpoint = Column(String, nullable=False, index=True)
    client_id = Column(String, nullable=False, index=True)
    expires_at = Column(BigInteger, nullable=False, index=True)
    caster_host = Column(String, nullable=True, index=True)
    created_at = Column(BigInteger, nullable=False)
    last_heartbeat_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_rtcm_serial_expires', 'serial', 'expires_at'),
        Index('ix_rtcm_serial_mountpoint_expires', 'serial', 'mountpoint', 'expires_at'),
        Index('ix_rtcm_client_serial', 'client_id', 'serial'),
    )
