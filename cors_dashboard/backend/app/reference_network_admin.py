"""Administrator-only control for the optional RTCM probe."""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool
from . import auth
from .reference_network_settings import get_check_settings, set_rtcm_check_enabled

router = APIRouter()


class ReferenceNetworkSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rtcm_check_enabled: StrictBool


def _admin(user):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Chỉ quản trị viên được thay đổi kiểm tra CORS Cục")


@router.get("/api/admin/reference-network-settings")
async def reference_network_settings(response: Response, user=Depends(auth.get_current_user)):
    _admin(user)
    response.headers["Cache-Control"] = "no-store"
    return get_check_settings()


@router.put("/api/admin/reference-network-settings")
async def update_reference_network_settings(body: ReferenceNetworkSettingsUpdate, response: Response,
                                            user=Depends(auth.get_current_user)):
    _admin(user)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await asyncio.to_thread(set_rtcm_check_enabled, body.rtcm_check_enabled)
    except OSError:
        raise HTTPException(503, "Chưa lưu được cài đặt kiểm tra CORS Cục")
