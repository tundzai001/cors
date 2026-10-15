"""FastAPI router for real-time GNSS satellite orbital elements."""

from typing import Optional
from fastapi import APIRouter, Query, Response

from .satellite_service import satellite_service

router = APIRouter(prefix="/api/gnss/satellites", tags=["satellites"])


@router.get("/tle")
async def get_satellites_tle(
    response: Response,
    force: bool = Query(False, description="Force refresh TLE elements from CelesTrak"),
):
    """Return cached orbital TLE elements for GPS, Galileo, BeiDou, and GLONASS."""
    data = await satellite_service.get_satellites_tle(force_refresh=force)
    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=7200"
    return data


@router.get("/summary")
async def get_satellites_summary(response: Response):
    """Return operational GNSS satellite counts and TLE status."""
    summary = await satellite_service.get_summary()
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return summary
