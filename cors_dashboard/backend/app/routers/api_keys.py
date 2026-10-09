"""Admin-managed tokens for server-to-server station reads."""

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth, models
from ..database import get_auth_db, get_db

router = APIRouter(tags=["API keys"])


STATION_FIELDS = frozenset({
    "name", "status", "timestamp", "offline_since", "lat", "lon", "alt",
    "mountpoint", "streams", "ntrip_status", "is_provisioned",
    "ntrip_connected", "bps", "service_config",
})


class ApiKeyPolicy(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: int = Field(ge=0, description="Unix timestamp UTC; 0 means no expiry")
    all_stations: bool = False
    station_serials: list[str] = Field(default_factory=list)
    data_fields: list[str] = Field(default_factory=list)
    edit_config: bool = False


async def _validate_policy(body: ApiKeyPolicy, db: AsyncSession) -> dict:
    now = int(time.time())
    if body.expires_at != 0 and body.expires_at <= now:
        raise HTTPException(422, "Expiry must be in the future")
    fields = sorted(set(body.data_fields))
    if not fields or not set(fields) <= STATION_FIELDS:
        raise HTTPException(422, "Select valid station data fields")
    serials = [] if body.all_stations else sorted({serial.strip() for serial in body.station_serials if serial.strip()})
    if not body.all_stations and not serials:
        raise HTTPException(422, "Select at least one station")
    if serials:
        known = set((await db.execute(select(models.Device.serial).where(models.Device.serial.in_(serials)))).scalars().all())
        if known != set(serials):
            raise HTTPException(422, "Unknown station serial")
    return {
        "name": body.name.strip(), "expires_at": body.expires_at,
        "all_stations": body.all_stations,
        "station_serials": serials,
        "data_fields": fields, "edit_config": body.edit_config,
    }


def _key_metadata(key: models.ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "created_at": key.created_at,
        "expires_at": key.expires_at,
        "revoked_at": key.revoked_at,
        "created_by": key.created_by,
        "all_stations": key.all_stations,
        "station_serials": key.station_serials,
        "data_fields": key.data_fields,
        "edit_config": key.edit_config,
    }


def project_station_items(items: list[dict], key: models.ApiKey, configs: dict) -> list[dict]:
    """Enforce station and field scopes after loading the shared map cache."""
    allowed_serials = set(key.station_serials or [])
    fields = set(key.data_fields or []) & STATION_FIELDS
    return [
        {
            "serial": item["serial"],
            **{field: item[field] for field in fields if field in item},
            **({"service_config": configs.get(item["serial"]) or {}} if "service_config" in fields else {}),
        }
        for item in items
        if key.all_stations or item["serial"] in allowed_serials
    ]


def require_station_edit(key: models.ApiKey, serial: str) -> None:
    if not key.edit_config or (not key.all_stations and serial not in (key.station_serials or [])):
        raise HTTPException(403, "API key cannot edit this station")


@router.get("/api/admin/api-keys")
async def list_api_keys(
    db: AsyncSession = Depends(get_auth_db),
    user: models.User = Depends(auth.get_current_user),
):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Admin access required")
    result = await db.execute(select(models.ApiKey).order_by(models.ApiKey.id.desc()))
    return [_key_metadata(key) for key in result.scalars().all()]


@router.post("/api/admin/api-keys", status_code=201)
async def create_api_key(
    body: ApiKeyPolicy,
    response: Response,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Admin access required")
    policy = await _validate_policy(body, devices_db)
    if not policy["name"]:
        raise HTTPException(422, "Name is required")
    token = "cors_" + secrets.token_urlsafe(32)
    key = models.ApiKey(
        **policy,
        key_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_at=int(time.time()),
        created_by=user.id,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    response.headers["Cache-Control"] = "no-store"
    return {**_key_metadata(key), "token": token}


@router.put("/api/admin/api-keys/{key_id}")
async def update_api_key(
    key_id: int,
    body: ApiKeyPolicy,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Admin access required")
    key = await db.get(models.ApiKey, key_id)
    if key is None or key.revoked_at is not None:
        raise HTTPException(404, "Active API key not found")
    policy = await _validate_policy(body, devices_db)
    if not policy["name"]:
        raise HTTPException(422, "Name is required")
    for field, value in policy.items():
        setattr(key, field, value)
    await db.commit()
    return _key_metadata(key)


@router.delete("/api/admin/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_auth_db),
    user: models.User = Depends(auth.get_current_user),
):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Admin access required")
    key = await db.get(models.ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "API key not found")
    if key.revoked_at is None:
        key.revoked_at = int(time.time())
        await db.commit()


async def require_integration_key(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_auth_db),
) -> models.ApiKey:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.startswith("cors_"):
        raise HTTPException(401, "Valid integration token required", headers={"WWW-Authenticate": "Bearer"})
    digest = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(select(models.ApiKey).where(
        models.ApiKey.key_hash == digest,
        models.ApiKey.revoked_at.is_(None),
        or_(models.ApiKey.expires_at == 0, models.ApiKey.expires_at > int(time.time())),
    ))
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(401, "Invalid, expired or revoked integration token", headers={"WWW-Authenticate": "Bearer"})
    return key
