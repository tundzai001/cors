"""Station map read APIs and their public snapshot cache."""

import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi.responses import Response
import httpx
import json
import os
import time
from .administrative_catalog import name_matches_query, search_catalog, catalog

from . import auth, crud, models, schemas

PROVINCE_DATASET_PATTERN = '^(current|legacy)$'
from .database import get_auth_db, get_db
from .operations_routes import _device_total_bps
from .status_reconciler import normalize_station_snapshot

logger = logging.getLogger("app.main")


PUBLIC_MAP_CACHE_TTL_SECONDS = max(
    1.0,
    float(os.getenv("PUBLIC_MAP_CACHE_TTL_SECONDS", "10")),
)
public_map_cache: Optional[list] = None
public_map_cache_updated_monotonic = 0.0

async def _get_map_station_items(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    global public_map_cache, public_map_cache_updated_monotonic

    response.headers["Cache-Control"] = (
        f"public, max-age={int(PUBLIC_MAP_CACHE_TTL_SECONDS)}, stale-while-revalidate=30"
    )
    now_monotonic = time.monotonic()
    if (
        public_map_cache is not None
        and now_monotonic - public_map_cache_updated_monotonic < PUBLIC_MAP_CACHE_TTL_SECONDS
    ):
        public_map_cache = [
            {key: value for key, value in item.items() if key != "service_config"}
            for item in public_map_cache
            if not crud.is_blocked_device_serial(item.get("serial"))
        ]
        response.headers["X-Data-Cache"] = "HIT"
        return [normalize_station_snapshot(item) for item in public_map_cache]

    try:
        # Lấy tất cả thiết bị
        result = await db.execute(select(models.Device))
        devices = [d for d in result.scalars().all() if not crud.is_blocked_device_serial(d.serial)]

        def _dict_or_empty(value: Any) -> dict:
            return value if isinstance(value, dict) else {}

        def _extract_public_map_coords(dev: models.Device) -> tuple[Optional[Any], Optional[Any], Optional[Any]]:
            candidate_sources = []

            base_config = _dict_or_empty(getattr(dev, "base_config", None))
            if base_config:
                candidate_sources.append(base_config)

            base_coords = getattr(dev, "base_coords", None)
            if isinstance(base_coords, dict):
                candidate_sources.append(base_coords)

            for source in candidate_sources:
                coords = _dict_or_empty(source.get("coords"))
                lat = coords.get("lat") if coords.get("lat") is not None else source.get("lat")
                lon = coords.get("lon") if coords.get("lon") is not None else source.get("lon")
                alt = coords.get("alt") if coords.get("alt") is not None else source.get("alt")

                if lat is not None and lon is not None:
                    try:
                        alt_val = float(alt) if alt is not None else 0.0
                    except (TypeError, ValueError):
                        alt_val = 0.0
                    return lat, lon, alt_val

            return None, None, None

        def _extract_public_mountpoint(dev: models.Device) -> Optional[str]:
            service_config = _dict_or_empty(getattr(dev, "service_config", None))
            for key in ("active_mountpoint", "mountpoint1", "mountpoint2"):
                value = str(service_config.get(key) or "").strip()
                if value:
                    return value
            return None

        def _extract_public_streams(dev: models.Device) -> list:
            service_config = _dict_or_empty(getattr(dev, "service_config", None))
            streams = []

            # Check S1
            mp1 = str(service_config.get("mountpoint1") or "").strip()
            if mp1:
                streams.append({
                    "stream_id": "s1",
                    "host": service_config.get("serverhost1", service_config.get("host1", "")),
                    "port": service_config.get("port1", service_config.get("serverport1", "")),
                    "mountpoint": mp1,
                    "enabled": bool(service_config.get("server1_enabled"))
                })

            # Check S2
            mp2 = str(service_config.get("mountpoint2") or "").strip()
            if mp2:
                streams.append({
                    "stream_id": "s2",
                    "host": service_config.get("serverhost2", service_config.get("host2", "")),
                    "port": service_config.get("port2", service_config.get("serverport2", "")),
                    "mountpoint": mp2,
                    "enabled": bool(service_config.get("server2_enabled"))
                })

            # Fallback
            if not streams:
                amp = str(service_config.get("active_mountpoint") or "").strip()
                if amp:
                    streams.append({
                        "stream_id": "active",
                        "host": "",
                        "port": "",
                        "mountpoint": amp,
                        "enabled": True
                    })

            return streams

        public_data = []
        skipped_devices = 0

        for dev in devices:
            lat, lon, alt = _extract_public_map_coords(dev)

            if lat is not None and lon is not None:
                try:
                    public_data.append({
                        "serial": dev.serial,
                        "name": dev.name,
                        "status": dev.status,
                        "timestamp": dev.timestamp,
                        "offline_since": dev.offline_since,
                        "lat": float(lat),
                        "lon": float(lon),
                        "alt": float(alt) if alt is not None else 0.0,
                        "mountpoint": _extract_public_mountpoint(dev),
                        "streams": _extract_public_streams(dev),
                        "ntrip_status": schemas.redact_config_secrets(dev.ntrip_status),
                        "is_provisioned": dev.is_provisioned,
                        "ntrip_connected": getattr(dev, "ntrip_connected", False),
                        "bps": _device_total_bps(dev)
                    })
                except (ValueError, TypeError) as e:
                    logging.warning(f"⚠️ Invalid coordinates for {dev.name}: {e}")
                    skipped_devices += 1
                    continue
            else:
                skipped_devices += 1

        public_map_cache = public_data
        public_map_cache_updated_monotonic = time.monotonic()
        response.headers["X-Data-Cache"] = "MISS"
        logger.debug(
            f"✓ Public map API: found {len(devices)} devices, returning {len(public_data)} with valid coords, "
            f"skipped {skipped_devices} without usable coords"
        )
        return [normalize_station_snapshot(item) for item in public_data]

    except Exception as e:
        logging.exception(f"â Œ Error fetching public map data: {e}")
        return []


get_public_map_stations_router = APIRouter()

@get_public_map_stations_router.get("/api/public/map-stations")
async def get_public_map_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    items = await _get_map_station_items(response, db)
    guest_fields = {
        "serial", "name", "status", "timestamp", "offline_since", "lat", "lon", "alt",
        "ntrip_connected", "bps", "is_provisioned",
    }
    return [{field: value for field, value in item.items() if field in guest_fields} for item in items]


_GEOCODE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_GEOCODE_CACHE_TTL = 3600.0  # 1 hour
_GEOCODE_CACHE_MAX_SIZE = 1000


def _geocode_matches_query(query: str, display_name: str) -> bool:
    return name_matches_query(query, display_name)


async def _fetch_geocode(query: str) -> list[dict[str, Any]]:
    cleaned = str(query or "").strip()
    if not cleaned:
        return []

    cache_key = cleaned.lower()
    now = time.monotonic()
    cached = _GEOCODE_CACHE.get(cache_key)
    if cached and (now - cached[0] < _GEOCODE_CACHE_TTL):
        return cached[1]

    known_places = search_catalog(cleaned, limit=1)
    # Restore a historical ward's district/province context before asking a geocoder.
    lookup_query = known_places[0]['display_name'] if known_places else cleaned
    results: list[dict[str, Any]] = []
    seen_coords: set[tuple[float, float]] = set()

    def _add_result(lat: float, lon: float, display_name: str, name: str, source: str, score: float = 100.0):
        if not _geocode_matches_query(cleaned, display_name):
            return
        # Round coordinates to ~11 meters to deduplicate near-identical hits
        coord_key = (round(lat, 4), round(lon, 4))
        if coord_key in seen_coords:
            return
        seen_coords.add(coord_key)
        results.append({
            "lat": lat,
            "lon": lon,
            "display_name": display_name,
            "name": name,
            "source": source,
            "score": score,
        })

    # 1. Tier 1: ESRI ArcGIS World Geocoding (high precision for Vietnam: streets, wards, districts, landmarks)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
                params={
                    "f": "json",
                    "singleLine": lookup_query,
                    "countryCode": "VNM",
                    "maxLocations": 5,
                    "outFields": "Match_addr,PlaceName,Type",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for c in data.get("candidates", []):
                    score = float(c.get("score", 0))
                    loc = c.get("location", {})
                    lat = float(loc.get("y", 0))
                    lon = float(loc.get("x", 0))
                    if score >= 70.0 and (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0):
                        addr = str(c.get("address") or cleaned).strip()
                        _add_result(lat, lon, addr, c.get("attributes", {}).get("PlaceName") or addr, "arcgis", score)
    except Exception as exc:
        logger.debug("ArcGIS geocode lookup failed for '%s': %s", cleaned, exc)

    # 2. Tier 2: Photon Komoot (OpenStreetMap Elasticsearch index - great for POIs, markets, airports)
    if len(results) < 3:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(
                    "https://photon.komoot.io/api/",
                    params={"q": lookup_query, "limit": 4},
                    headers={"User-Agent": "Aitogy-CORS-System/1.0 (contact@aitogy.com)"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for f in data.get("features", []):
                        props = f.get("properties", {})
                        country = str(props.get("country", "")).lower()
                        coords = f.get("geometry", {}).get("coordinates", [])
                        if len(coords) == 2:
                            lon, lat = float(coords[0]), float(coords[1])
                            if country in ("việt nam", "vietnam", "vn") or (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0):
                                parts = [props.get("name"), props.get("street"), props.get("district"), props.get("city"), props.get("state"), "Việt Nam"]
                                display = ", ".join(dict.fromkeys(p for p in parts if p))
                                _add_result(lat, lon, display, props.get("name") or cleaned, "photon", 85.0)
        except Exception as exc:
            logger.debug("Photon geocode lookup failed for '%s': %s", cleaned, exc)

    # 3. Tier 3: Open-Meteo Geocoding (settlements, towns, cities)
    if not results:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": cleaned, "count": 5, "language": "vi", "format": "json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", []):
                        lat = float(item.get("latitude", 0))
                        lon = float(item.get("longitude", 0))
                        country_code = str(item.get("country_code", "")).upper()
                        if country_code == "VN" or (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0):
                            parts = [item.get("name"), item.get("admin2"), item.get("admin1"), "Việt Nam"]
                            display_name = ", ".join(p for p in parts if p)
                            _add_result(lat, lon, display_name, item.get("name", cleaned), "open-meteo", 80.0)
        except Exception as exc:
            logger.debug("Open-Meteo geocode lookup failed for '%s': %s", cleaned, exc)

    # 4. Tier 4: Nominatim fallback
    if not results:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                query_vn = f"{lookup_query}, Viet Nam"
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "format": "jsonv2",
                        "limit": "5",
                        "countrycodes": "vn",
                        "viewbox": "102,24,110,8",
                        "bounded": "1",
                        "addressdetails": "1",
                        "namedetails": "1",
                        "dedupe": "1",
                        "accept-language": "vi,en",
                        "q": query_vn,
                    },
                    headers={"User-Agent": "Aitogy-CORS-System/1.0 (contact@aitogy.com)"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            lat = float(item.get("lat", 0))
                            lon = float(item.get("lon", 0))
                            if 8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0:
                                _add_result(lat, lon, item.get("display_name", cleaned), item.get("name") or cleaned, "nominatim", 75.0)
        except Exception as exc:
            logger.debug("Nominatim geocode lookup failed for '%s': %s", cleaned, exc)

    if len(_GEOCODE_CACHE) >= _GEOCODE_CACHE_MAX_SIZE:
        _GEOCODE_CACHE.clear()
    _GEOCODE_CACHE[cache_key] = (now, results)

    return results


@get_public_map_stations_router.get("/api/public/geocode")
async def public_geocode(
    q: str = Query(..., min_length=1, max_length=200, description="Tên địa điểm tìm kiếm"),
):
    """Proxy geocoding lookup for Vietnam station map search."""
    return await _fetch_geocode(q)


_PROVINCES_GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "data", "vietnam_provinces.geojson")
_SRC_GIS_GEOJSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "gis", "vietnam_provinces.geojson"))
_PROVINCES_GEOJSON_BYTES: Optional[bytes] = None
_PROVINCES_SUMMARY: Optional[list[dict[str, Any]]] = None


def _get_provinces_geojson_bytes() -> bytes:
    global _PROVINCES_GEOJSON_BYTES
    if _PROVINCES_GEOJSON_BYTES is None:
        target_path = _PROVINCES_GEOJSON_PATH if os.path.exists(_PROVINCES_GEOJSON_PATH) else _SRC_GIS_GEOJSON_PATH
        if os.path.exists(target_path):
            with open(target_path, "rb") as f:
                _PROVINCES_GEOJSON_BYTES = f.read()
        else:
            _PROVINCES_GEOJSON_BYTES = b'{"type":"FeatureCollection","features":[]}'
    return _PROVINCES_GEOJSON_BYTES


def _get_provinces_summary() -> list[dict[str, Any]]:
    global _PROVINCES_SUMMARY
    if _PROVINCES_SUMMARY is None:
        try:
            from .province_db_service import UNITS_JSON_PATH
            if os.path.exists(UNITS_JSON_PATH):
                with open(UNITS_JSON_PATH, "r", encoding="utf-8") as f:
                    units = json.load(f)
                items = [
                    {
                        "code": p.get("Code"),
                        "name": p.get("Name"),
                        "fullName": p.get("FullName"),
                        "nameEn": p.get("NameEn"),
                        "codeName": p.get("CodeName"),
                        "districtsCount": len(p.get("District") or []),
                    }
                    for p in units
                ]
                _PROVINCES_SUMMARY = sorted(items, key=lambda x: str(x.get("name", "")))
        except Exception as e:
            logger.debug("Error generating provinces summary: %s", e)
        if not _PROVINCES_SUMMARY:
            _PROVINCES_SUMMARY = []
    return _PROVINCES_SUMMARY


@get_public_map_stations_router.get("/api/public/vietnam-provinces-geojson")
async def get_vietnam_provinces_geojson():
    """Return Vietnam province boundaries GeoJSON for Leaflet overlay."""
    content = _get_provinces_geojson_bytes()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"},
    )


_WORLD_COUNTRIES_GEOJSON_BYTES: bytes | None = None

def _get_world_countries_geojson_bytes() -> bytes:
    global _WORLD_COUNTRIES_GEOJSON_BYTES
    if _WORLD_COUNTRIES_GEOJSON_BYTES is None:
        target_path = os.path.join(os.path.dirname(__file__), "data", "world_countries.geojson")
        if os.path.exists(target_path):
            with open(target_path, "rb") as f:
                _WORLD_COUNTRIES_GEOJSON_BYTES = f.read()
        else:
            _WORLD_COUNTRIES_GEOJSON_BYTES = b'{"type":"FeatureCollection","features":[]}'
    return _WORLD_COUNTRIES_GEOJSON_BYTES


@get_public_map_stations_router.get("/api/public/world-countries-geojson")
async def get_world_countries_geojson():
    """Return world country boundaries GeoJSON for 3D Globe overlay."""
    content = _get_world_countries_geojson_bytes()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"},
    )


@get_public_map_stations_router.get("/api/public/vietnam-provinces")
async def get_vietnam_provinces(
    dataset: str = Query('current', pattern=PROVINCE_DATASET_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    """Read the upstream SQL columns; historical codes stay in a separate catalogue."""
    if dataset == 'legacy':
        from .province_db_service import list_legacy_units
        rows = await list_legacy_units(db, 'province')
    else:
        try:
            result = await db.execute(select(
                models.Province.code, models.Province.name, models.Province.full_name,
                models.Province.name_en, models.Province.code_name, models.Province.postal_code_prefix,
            ).order_by(models.Province.name))
            rows = [dict(r) for r in result.mappings().all()]
        except Exception as exc:
            logger.debug("Province SQL read unavailable: %s", exc)
            rows = []
        if not rows:
            return _get_provinces_summary()
    return [dict(code=r['code'], name=r['name'], fullName=r['full_name'],
                 nameEn=r.get('name_en'), codeName=r.get('code_name'),
                 postalCodePrefix=r.get('postal_code_prefix'),
                 lat=None, lon=None, dataset=dataset) for r in rows]


@get_public_map_stations_router.get("/api/public/vietnam-districts")
async def get_vietnam_districts(
    province_code: str = Query(..., min_length=1, max_length=20),
    dataset: str = Query('current', pattern=PROVINCE_DATASET_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    """The current two-tier model has no districts; expose historical districts explicitly."""
    if dataset != 'legacy':
        return []
    from .province_db_service import list_legacy_units
    rows = await list_legacy_units(db, 'district', province_code=province_code)
    return [dict(code=r['code'], name=r['name'], fullName=r['full_name'],
                 provinceCode=r['province_code'], lat=None, lon=None, dataset='legacy')
            for r in rows]


@get_public_map_stations_router.get("/api/public/vietnam-wards")
async def get_vietnam_wards(
    province_code: Optional[str] = Query(None, min_length=1, max_length=20),
    district_code: Optional[str] = Query(None, min_length=1, max_length=20),
    dataset: str = Query('current', pattern=PROVINCE_DATASET_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    """Read wards without assuming optional GIS columns exist in the SQL import."""
    rows = []
    if dataset == 'legacy':
        from .province_db_service import list_legacy_units
        rows = await list_legacy_units(db, 'ward', province_code, district_code)
    if dataset == 'current' and not district_code:
        try:
            statement = select(models.Ward.code, models.Ward.name, models.Ward.full_name,
                               models.Ward.province_code)
            if province_code:
                statement = statement.where(models.Ward.province_code == province_code)
            result = await db.execute(statement.order_by(models.Ward.name))
            rows = [dict(r) for r in result.mappings().all()]
        except Exception as exc:
            logger.debug("Ward SQL read unavailable: %s", exc)
    if not rows:
        rows = [r for r in catalog(dataset) if r['type'] == 'ward'
                and (not province_code or r['province_code'] == province_code)
                and (not district_code or r['district_code'] == district_code)]
    return [dict(code=r['code'], name=r['name'], fullName=r['full_name'],
                 districtCode=r.get('district_code'), provinceCode=r['province_code'],
                 lat=None, lon=None, dataset=dataset) for r in rows]


@get_public_map_stations_router.get("/api/public/search-administrative-units")
async def search_admin_units(
    q: str = Query(..., min_length=1, max_length=100, description="Tên xã, phường, quận, huyện hoặc tỉnh thành"),
    dataset: str = Query("all", pattern="^(all|current|legacy)$"),
    db: AsyncSession = Depends(get_db),
):
    """Search provinces, districts, and wards in database by name with exact coordinates."""
    from .province_db_service import search_administrative_units
    return await search_administrative_units(db, q, dataset=dataset)


@get_public_map_stations_router.post("/api/admin/seed-vietnam-units")
async def seed_vietnam_units_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    """Seed Vietnam administrative provinces and wards into database."""
    from .province_db_service import seed_vietnam_provinces
    return await seed_vietnam_provinces(db)


get_authenticated_map_stations_router = APIRouter()

@get_authenticated_map_stations_router.get("/api/map/stations")
async def get_authenticated_map_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_CONFIG)),
):
    """Return map data and in-scope station config to authenticated users."""
    public_items = await _get_map_station_items(response, db)
    response.headers["Cache-Control"] = "private, no-store"

    visible_serials = None
    if current_user.role == auth.Role.COORDINATOR:
        visible_serials = await crud.get_coordinator_visible_base_serials(db, current_user)
    elif current_user.role == auth.Role.VIEWER:
        visible_serials = await crud.get_viewer_visible_base_serials(auth_db, db, current_user)
    elif current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem cấu hình trạm.")

    result = await db.execute(select(models.Device))
    devices = {
        str(device.serial).strip().upper(): device
        for device in result.scalars().all()
        if device.serial
    }
    items = []
    for public_item in public_items:
        serial = str(public_item.get("serial") or "").strip().upper()
        if not serial or (visible_serials is not None and serial not in visible_serials):
            continue
        device = devices.get(serial)
        if not device:
            continue
        item = dict(public_item)
        config = device.service_config if isinstance(device.service_config, dict) else {}
        can_reveal_config = (
            current_user.role == auth.Role.ADMIN
            or (current_user.role == auth.Role.COORDINATOR and device.user_id == current_user.id)
        )
        item["service_config"] = config if can_reveal_config else schemas.redact_config_secrets(config)
        item["detected_chip_type"] = device.detected_chip_type
        item["system_info"] = schemas.redact_config_secrets(device.system_info)
        items.append(item)
    return items


get_public_network_summary_router = APIRouter()

@get_public_network_summary_router.get("/api/public/network-summary")
async def get_public_network_summary(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(models.Device))
        devices = [d for d in result.scalars().all() if not crud.is_blocked_device_serial(d.serial)]
        active_statuses = {"online", "sleep", "streaming", "starting"}
        return {
            "total_stations": len(devices),
            "active_stations": sum(1 for dev in devices if str(dev.status or "").lower() in active_statuses),
            "coverage_areas": len({
                str(dev.location).split(",")[-1].strip()
                for dev in devices
                if getattr(dev, "location", None)
            })
        }
    except Exception as e:
        logging.exception(f"Error fetching public network summary: {e}")
        return {"total_stations": 0, "active_stations": 0, "coverage_areas": 0}

