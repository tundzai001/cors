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


def _map_config(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _map_coordinate_values(source: dict) -> tuple[Optional[Any], Optional[Any], Optional[Any]]:
    coords = _map_config(source.get("coords"))
    lat = coords.get("lat") if coords.get("lat") is not None else source.get("lat")
    lon = coords.get("lon") if coords.get("lon") is not None else source.get("lon")
    alt = coords.get("alt") if coords.get("alt") is not None else source.get("alt")
    return lat, lon, alt


def _map_altitude(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _public_map_coords(dev: models.Device) -> tuple[Optional[Any], Optional[Any], Optional[Any]]:
    sources = []
    base_config = _map_config(getattr(dev, "base_config", None))
    if base_config:
        sources.append(base_config)
    base_coords = getattr(dev, "base_coords", None)
    if isinstance(base_coords, dict):
        sources.append(base_coords)

    for source in sources:
        lat, lon, alt = _map_coordinate_values(source)
        if lat is None or lon is None:
            continue
        return lat, lon, _map_altitude(alt)
    return None, None, None


def _public_map_mountpoint(dev: models.Device) -> Optional[str]:
    config = _map_config(getattr(dev, "service_config", None))
    for key in ("active_mountpoint", "mountpoint1", "mountpoint2"):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    return None


def _public_map_streams(dev: models.Device) -> list:
    config = _map_config(getattr(dev, "service_config", None))
    streams = []
    for index, stream_id in ((1, "s1"), (2, "s2")):
        mountpoint = str(config.get(f"mountpoint{index}") or "").strip()
        if not mountpoint:
            continue
        streams.append({
            "stream_id": stream_id,
            "host": config.get(f"serverhost{index}", config.get(f"host{index}", "")),
            "port": config.get(f"port{index}", config.get(f"serverport{index}", "")),
            "mountpoint": mountpoint,
            "enabled": bool(config.get(f"server{index}_enabled")),
        })
    if streams:
        return streams
    mountpoint = str(config.get("active_mountpoint") or "").strip()
    if not mountpoint:
        return streams
    return [{
        "stream_id": "active", "host": "", "port": "",
        "mountpoint": mountpoint, "enabled": True,
    }]


def _public_map_item(dev: models.Device) -> Optional[dict]:
    lat, lon, alt = _public_map_coords(dev)
    if lat is None or lon is None:
        return None
    return {
        "serial": dev.serial,
        "name": dev.name,
        "status": dev.status,
        "timestamp": dev.timestamp,
        "offline_since": dev.offline_since,
        "lat": float(lat),
        "lon": float(lon),
        "alt": float(alt) if alt is not None else 0.0,
        "mountpoint": _public_map_mountpoint(dev),
        "streams": _public_map_streams(dev),
        "ntrip_status": schemas.redact_config_secrets(dev.ntrip_status),
        "is_provisioned": dev.is_provisioned,
        "ntrip_connected": getattr(dev, "ntrip_connected", False),
        "bps": _device_total_bps(dev),
    }


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
        public_data = []
        skipped_devices = 0

        for dev in devices:
            try:
                item = _public_map_item(dev)
            except (ValueError, TypeError) as e:
                logging.warning(f"⚠️ Invalid coordinates for {dev.name}: {e}")
                skipped_devices += 1
                continue
            if item is None:
                skipped_devices += 1
                continue
            public_data.append(item)

        public_map_cache = public_data
        public_map_cache_updated_monotonic = time.monotonic()
        response.headers["X-Data-Cache"] = "MISS"
        logger.debug(
            f"✓ Public map API: found {len(devices)} devices, returning {len(public_data)} with valid coords, "
            f"skipped {skipped_devices} without usable coords"
        )
        return [normalize_station_snapshot(item) for item in public_data]

    except Exception as e:
        logging.exception(f"❌ Error fetching public map data: {e}")
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


def _add_geocode_result(results, seen_coords, query, lat, lon, display_name, name, source, score=100.0):
    if not _geocode_matches_query(query, display_name):
        return
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


async def _fetch_arcgis_geocodes(lookup_query, cleaned, results, seen_coords):
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
                params={
                    "f": "json", "singleLine": lookup_query, "countryCode": "VNM",
                    "maxLocations": 5, "outFields": "Match_addr,PlaceName,Type",
                },
            )
            if response.status_code != 200:
                return
            for candidate in response.json().get("candidates", []):
                score = float(candidate.get("score", 0))
                location = candidate.get("location", {})
                lat = float(location.get("y", 0))
                lon = float(location.get("x", 0))
                if score < 70.0 or not (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0):
                    continue
                address = str(candidate.get("address") or cleaned).strip()
                _add_geocode_result(
                    results, seen_coords, cleaned, lat, lon, address,
                    candidate.get("attributes", {}).get("PlaceName") or address, "arcgis", score,
                )
    except Exception as exc:
        logger.debug("ArcGIS geocode lookup failed for '%s': %s", cleaned, exc)


async def _fetch_photon_geocodes(lookup_query, cleaned, results, seen_coords):
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                "https://photon.komoot.io/api/",
                params={"q": lookup_query, "limit": 4},
                headers={"User-Agent": "Aitogy-CORS-System/1.0 (contact@aitogy.com)"},
            )
            if response.status_code != 200:
                return
            for feature in response.json().get("features", []):
                properties = feature.get("properties", {})
                country = str(properties.get("country", "")).lower()
                coordinates = feature.get("geometry", {}).get("coordinates", [])
                if len(coordinates) != 2:
                    continue
                lon, lat = float(coordinates[0]), float(coordinates[1])
                in_vietnam = country in ("việt nam", "vietnam", "vn") or (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0)
                if not in_vietnam:
                    continue
                parts = [properties.get("name"), properties.get("street"), properties.get("district"),
                         properties.get("city"), properties.get("state"), "Việt Nam"]
                display = ", ".join(dict.fromkeys(part for part in parts if part))
                _add_geocode_result(
                    results, seen_coords, cleaned, lat, lon, display,
                    properties.get("name") or cleaned, "photon", 85.0,
                )
    except Exception as exc:
        logger.debug("Photon geocode lookup failed for '%s': %s", cleaned, exc)


async def _fetch_open_meteo_geocodes(cleaned, results, seen_coords):
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": cleaned, "count": 5, "language": "vi", "format": "json"},
            )
            if response.status_code != 200:
                return
            for item in response.json().get("results", []):
                lat = float(item.get("latitude", 0))
                lon = float(item.get("longitude", 0))
                country_code = str(item.get("country_code", "")).upper()
                in_vietnam = country_code == "VN" or (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0)
                if not in_vietnam:
                    continue
                parts = [item.get("name"), item.get("admin2"), item.get("admin1"), "Việt Nam"]
                display_name = ", ".join(part for part in parts if part)
                _add_geocode_result(
                    results, seen_coords, cleaned, lat, lon, display_name,
                    item.get("name", cleaned), "open-meteo", 80.0,
                )
    except Exception as exc:
        logger.debug("Open-Meteo geocode lookup failed for '%s': %s", cleaned, exc)


async def _fetch_nominatim_geocodes(lookup_query, cleaned, results, seen_coords):
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "format": "jsonv2", "limit": "5", "countrycodes": "vn",
                    "viewbox": "102,24,110,8", "bounded": "1", "addressdetails": "1",
                    "namedetails": "1", "dedupe": "1", "accept-language": "vi,en",
                    "q": f"{lookup_query}, Viet Nam",
                },
                headers={"User-Agent": "Aitogy-CORS-System/1.0 (contact@aitogy.com)"},
            )
            if response.status_code != 200:
                return
            data = response.json()
            if not isinstance(data, list):
                return
            for item in data:
                lat = float(item.get("lat", 0))
                lon = float(item.get("lon", 0))
                if not (8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0):
                    continue
                _add_geocode_result(
                    results, seen_coords, cleaned, lat, lon,
                    item.get("display_name", cleaned), item.get("name") or cleaned,
                    "nominatim", 75.0,
                )
    except Exception as exc:
        logger.debug("Nominatim geocode lookup failed for '%s': %s", cleaned, exc)


async def _fetch_geocode(query: str) -> list[dict[str, Any]]:
    cleaned = str(query or "").strip()
    if not cleaned:
        return []
    cache_key = cleaned.lower()
    now = time.monotonic()
    cached = _GEOCODE_CACHE.get(cache_key)
    if cached and now - cached[0] < _GEOCODE_CACHE_TTL:
        return cached[1]

    known_places = search_catalog(cleaned, limit=1)
    lookup_query = known_places[0]['display_name'] if known_places else cleaned
    results: list[dict[str, Any]] = []
    seen_coords: set[tuple[float, float]] = set()
    await _fetch_arcgis_geocodes(lookup_query, cleaned, results, seen_coords)
    if len(results) < 3:
        await _fetch_photon_geocodes(lookup_query, cleaned, results, seen_coords)
    if not results:
        await _fetch_open_meteo_geocodes(cleaned, results, seen_coords)
    if not results:
        await _fetch_nominatim_geocodes(lookup_query, cleaned, results, seen_coords)

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
    return [{'code':r['code'], 'name':r['name'], 'fullName':r['full_name'],
                 'nameEn':r.get('name_en'), 'codeName':r.get('code_name'),
                 'postalCodePrefix':r.get('postal_code_prefix'),
                 'lat':None, 'lon':None, 'dataset':dataset} for r in rows]


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
    return [{'code':r['code'], 'name':r['name'], 'fullName':r['full_name'],
                 'provinceCode':r['province_code'], 'lat':None, 'lon':None, 'dataset':'legacy'}
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
    return [{'code':r['code'], 'name':r['name'], 'fullName':r['full_name'],
                 'districtCode':r.get('district_code'), 'provinceCode':r['province_code'],
                 'lat':None, 'lon':None, 'dataset':dataset} for r in rows]


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

@get_authenticated_map_stations_router.get("/api/map/stations", responses={403: {"description": "Forbidden"}})
async def _authenticated_map_visible_serials(current_user, db, auth_db):
    if current_user.role == auth.Role.COORDINATOR:
        return await crud.get_coordinator_visible_base_serials(db, current_user)
    if current_user.role == auth.Role.VIEWER:
        return await crud.get_viewer_visible_base_serials(auth_db, db, current_user)
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem cấu hình trạm.")
    return None


def _map_device_index(devices):
    return {
        str(device.serial).strip().upper(): device
        for device in devices
        if device.serial
    }


def _authenticated_map_station_item(public_item, devices, visible_serials, current_user):
    serial = str(public_item.get("serial") or "").strip().upper()
    if not serial or (visible_serials is not None and serial not in visible_serials):
        return None
    device = devices.get(serial)
    if not device:
        return None
    item = dict(public_item)
    config = device.service_config if isinstance(device.service_config, dict) else {}
    can_reveal_config = (
        current_user.role == auth.Role.ADMIN
        or (current_user.role == auth.Role.COORDINATOR and device.user_id == current_user.id)
    )
    item["service_config"] = config if can_reveal_config else schemas.redact_config_secrets(config)
    item["detected_chip_type"] = device.detected_chip_type
    item["system_info"] = schemas.redact_config_secrets(device.system_info)
    return item


async def get_authenticated_map_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_CONFIG)),
):
    """Return map data and in-scope station config to authenticated users."""
    public_items = await _get_map_station_items(response, db)
    response.headers["Cache-Control"] = "private, no-store"
    visible_serials = await _authenticated_map_visible_serials(current_user, db, auth_db)
    result = await db.execute(select(models.Device))
    devices = _map_device_index(result.scalars().all())
    return [
        item for public_item in public_items
        if (item := _authenticated_map_station_item(public_item, devices, visible_serials, current_user)) is not None
    ]


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

