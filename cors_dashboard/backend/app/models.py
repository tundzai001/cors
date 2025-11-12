# ==============================================================================
# == backend/app/models.py - CẢI TIẾN VỚI DATABASE INDEXES                  ==
# ==============================================================================

from sqlalchemy import Column, String, Integer, BigInteger, Boolean, JSON, Index
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
    base_config = Column(JSON, nullable=True)
    service_config = Column(JSON, nullable=True)
    ntrip_connected = Column(Boolean, default=False)
    ntrip_status = Column(JSON, nullable=True)
    is_locked = Column(Boolean, default=False)
    
    __table_args__ = (
        Index('ix_user_status', 'user_id', 'status'),
        Index('ix_status_timestamp', 'status', 'timestamp'),
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