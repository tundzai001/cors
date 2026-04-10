# ==============================================================================
# == backend/app/models.py ==
# ==============================================================================

from sqlalchemy import Column, String, Integer, BigInteger, Boolean, JSON, Index
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base, AuthBase 

class Device(Base):
    __tablename__ = "devices"

    serial = Column(String, primary_key=True, index=True)
    name = Column(String, index=True)
    status = Column(String, default="offline", index=True)
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
    created_at = Column(Integer)
    
class User(AuthBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="viewer", index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(BigInteger, nullable=False)
    can_create_rovers = Column(Boolean, default=False, nullable=False)

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