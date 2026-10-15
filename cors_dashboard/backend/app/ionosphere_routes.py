"""FastAPI routes for NOAA GloTEC real-time ionosphere TEC data."""

from typing import Optional
from fastapi import APIRouter, Query, Response

from .ionosphere_service import ionosphere_service

router = APIRouter(prefix="/api/ionosphere", tags=["ionosphere"])


@router.get("/latest")
async def get_latest_ionosphere(
    response: Response,
    format: Optional[str] = Query("compact", description="Format: 'compact' (72x72 grid) or 'geojson' (FeatureCollection)"),
    force: bool = Query(False, description="Force fetch fresh data from NOAA"),
):
    """Return latest NOAA GloTEC 2D real-time ionosphere Total Electron Content (TEC) data."""
    format_type = "geojson" if str(format).lower() == "geojson" else "compact"
    data = await ionosphere_service.get_latest(format_type=format_type, force_refresh=force)

    # Set cache headers for proxy/browser caching (5 mins max age)
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    if data.get("is_stale"):
        response.headers["X-Ionosphere-Status"] = "STALE"
    else:
        response.headers["X-Ionosphere-Status"] = "ACTIVE"

    return data


@router.get("/status")
async def get_ionosphere_status(response: Response):
    """Return lightweight status and observation timestamps for ionosphere data."""
    status = await ionosphere_service.get_status()
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return status
