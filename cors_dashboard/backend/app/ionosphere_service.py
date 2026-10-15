"""NOAA GloTEC Ionosphere service for real-time TEC (Total Electron Content) overlay."""

import asyncio
from datetime import datetime, timezone, timedelta
import logging
import time
from typing import Any, Optional
import httpx

logger = logging.getLogger("app.ionosphere")

NOAA_CATALOG_URL = "https://services.swpc.noaa.gov/products/glotec/geojson_2d_urt.json"
NOAA_BASE_URL = "https://services.swpc.noaa.gov"
CACHE_TTL_SECONDS = 300.0  # 5 minutes cache TTL (NOAA cadence is ~10 minutes)
STALE_THRESHOLD_MINUTES = 90  # Mark stale if latest data is older than 90 mins

# Expected GloTEC 2D URT grid:
# Latitudes: 72 values from -88.75 to +88.75 (step 2.5)
# Longitudes: 72 values from -177.5 to +177.5 (step 5.0)
GRID_LATS = 72
GRID_LONS = 72
LAT_STEP = 2.5
LON_STEP = 5.0
LAT_MIN = -88.75
LAT_MAX = 88.75
LON_MIN = -177.5
LON_MAX = 177.5


class IonosphereService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached_compact: Optional[dict[str, Any]] = None
        self._cached_geojson: Optional[dict[str, Any]] = None
        self._last_catalog_fetch = 0.0
        self._last_catalog_items: list[dict[str, Any]] = []
        self._last_known_url: Optional[str] = None
        self._last_known_timetag: Optional[str] = None

    async def _fetch_catalog(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._last_catalog_items and (now - self._last_catalog_fetch < CACHE_TTL_SECONDS):
            return self._last_catalog_items

        resp = await client.get(NOAA_CATALOG_URL, timeout=12.0)
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list) or not items:
            raise ValueError("NOAA GloTEC catalog returned empty or invalid list")

        self._last_catalog_items = items
        self._last_catalog_fetch = now
        return items

    def _format_vietnam_time(self, time_tag_str: str) -> str:
        try:
            # e.g. "2026-10-15T08:35:00Z"
            dt_utc = datetime.fromisoformat(time_tag_str.replace("Z", "+00:00"))
            dt_vn = dt_utc.astimezone(timezone(timedelta(hours=7)))
            return dt_vn.strftime("%Y-%m-%d %H:%M:%S (UTC+7)")
        except Exception:
            return time_tag_str

    def _check_is_stale(self, time_tag_str: str) -> bool:
        try:
            dt_utc = datetime.fromisoformat(time_tag_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            age_minutes = (now_utc - dt_utc).total_seconds() / 60.0
            return age_minutes > STALE_THRESHOLD_MINUTES
        except Exception:
            return True

    def _parse_glotec_payload(self, raw_geojson: dict[str, Any], url: str) -> tuple[dict[str, Any], dict[str, Any]]:
        time_tag = raw_geojson.get("time_tag") or ""
        features = raw_geojson.get("features", [])

        obs_time_vn = self._format_vietnam_time(time_tag)
        is_stale = self._check_is_stale(time_tag)
        status = "STALE" if is_stale else "ACTIVE"

        # Organize 72x72 grid:
        # Rows: from North (+88.75) down to South (-88.75) so canvas top is North
        # Cols: from West (-177.5) to East (+177.5) so canvas left is West
        lats_set = set()
        lons_set = set()
        data_map: dict[tuple[float, float], tuple[float, float, float, int]] = {}
        tecs: list[float] = []
        anomalies: list[float] = []

        geojson_features = []

        for f in features:
            coords = f.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                continue
            lon = round(float(coords[0]), 2)
            lat = round(float(coords[1]), 2)
            lats_set.add(lat)
            lons_set.add(lon)

            props = f.get("properties", {})
            tec = float(props.get("tec", 0.0))
            anomaly = float(props.get("anomaly", 0.0))
            hmF2 = float(props.get("hmF2", 0.0))
            quality = int(props.get("quality_flag", 0))

            tecs.append(tec)
            anomalies.append(anomaly)
            data_map[(lat, lon)] = (tec, anomaly, hmF2, quality)

            geojson_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "tec": round(tec, 1),
                    "anomaly": round(anomaly, 2),
                    "hmF2": round(hmF2, 1),
                }
            })

        sorted_lats_asc = sorted(list(lats_set)) if lats_set else [round(LAT_MIN + i * LAT_STEP, 2) for i in range(GRID_LATS)]
        sorted_lons_asc = sorted(list(lons_set)) if lons_set else [round(LON_MIN + j * LON_STEP, 2) for j in range(GRID_LONS)]

        # North-to-South row ordering for direct canvas raster drawing
        sorted_lats_desc = list(reversed(sorted_lats_asc))

        tec_grid: list[list[float]] = []
        anomaly_grid: list[list[float]] = []

        for lat in sorted_lats_desc:
            t_row = []
            a_row = []
            for lon in sorted_lons_asc:
                val = data_map.get((lat, lon))
                if val:
                    t_row.append(round(val[0], 1))
                    a_row.append(round(val[1], 2))
                else:
                    t_row.append(0.0)
                    a_row.append(0.0)
            tec_grid.append(t_row)
            anomaly_grid.append(a_row)

        min_tec = round(min(tecs), 1) if tecs else 0.0
        max_tec = round(max(tecs), 1) if tecs else 0.0
        avg_tec = round(sum(tecs) / len(tecs), 1) if tecs else 0.0

        min_anom = round(min(anomalies), 2) if anomalies else 0.0
        max_anom = round(max(anomalies), 2) if anomalies else 0.0

        compact_payload = {
            "status": status,
            "is_stale": is_stale,
            "time_tag": time_tag,
            "observation_time_utc": time_tag,
            "observation_time_vn": obs_time_vn,
            "source_url": url,
            "source_name": "NOAA Space Weather Prediction Center (GloTEC URT)",
            "grid_info": {
                "rows": len(sorted_lats_desc),
                "cols": len(sorted_lons_asc),
                "lat_min": LAT_MIN,
                "lat_max": LAT_MAX,
                "lon_min": LON_MIN,
                "lon_max": LON_MAX,
                "lat_step": LAT_STEP,
                "lon_step": LON_STEP,
                "row_order": "north_to_south",
                "col_order": "west_to_east",
            },
            "stats": {
                "min_tec": min_tec,
                "max_tec": max_tec,
                "avg_tec": avg_tec,
                "min_anomaly": min_anom,
                "max_anomaly": max_anom,
            },
            "lats": sorted_lats_desc,
            "lons": sorted_lons_asc,
            "tec_grid": tec_grid,
            "anomaly_grid": anomaly_grid,
        }

        geojson_payload = {
            "type": "FeatureCollection",
            "metadata": {
                "status": status,
                "is_stale": is_stale,
                "time_tag": time_tag,
                "observation_time_vn": obs_time_vn,
                "stats": compact_payload["stats"],
            },
            "features": geojson_features,
        }

        return compact_payload, geojson_payload

    async def get_latest(self, format_type: str = "compact", force_refresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._cached_compact and (now - self._last_catalog_fetch < CACHE_TTL_SECONDS):
                if format_type == "geojson" and self._cached_geojson:
                    return self._cached_geojson
                return self._cached_compact

            try:
                async with httpx.AsyncClient(verify=False, timeout=18.0) as client:
                    items = await self._fetch_catalog(client)
                    latest_entry = items[-1]
                    url_path = latest_entry.get("url")
                    time_tag = latest_entry.get("time_tag")

                    # If timetag matches cached and we already have data, just renew TTL
                    if (
                        self._cached_compact
                        and time_tag
                        and time_tag == self._last_known_timetag
                        and not force_refresh
                    ):
                        self._last_catalog_fetch = now
                        if format_type == "geojson" and self._cached_geojson:
                            return self._cached_geojson
                        return self._cached_compact

                    full_url = NOAA_BASE_URL + url_path if str(url_path).startswith("/") else url_path
                    resp = await client.get(full_url, timeout=20.0)
                    resp.raise_for_status()
                    raw_geojson = resp.json()

                    compact, geojson = self._parse_glotec_payload(raw_geojson, full_url)

                    self._cached_compact = compact
                    self._cached_geojson = geojson
                    self._last_known_timetag = time_tag
                    self._last_known_url = full_url
                    self._last_catalog_fetch = now

                    if format_type == "geojson":
                        return geojson
                    return compact

            except Exception as exc:
                logger.warning("Error fetching NOAA GloTEC ionosphere data: %s", exc)
                # If cached data exists, return it with warning
                if self._cached_compact:
                    stale_compact = dict(self._cached_compact)
                    stale_compact["is_stale"] = True
                    stale_compact["status"] = "STALE"
                    stale_compact["warning"] = f"NOAA SWPC update temporarily unavailable: {exc}"
                    if format_type == "geojson" and self._cached_geojson:
                        stale_geo = dict(self._cached_geojson)
                        stale_geo["metadata"]["is_stale"] = True
                        stale_geo["metadata"]["status"] = "STALE"
                        return stale_geo
                    return stale_compact

                # No cache available
                fallback = {
                    "status": "UNAVAILABLE",
                    "is_stale": True,
                    "error": str(exc),
                    "time_tag": None,
                    "observation_time_vn": None,
                    "stats": {"min_tec": 0, "max_tec": 0, "avg_tec": 0},
                    "tec_grid": [],
                    "anomaly_grid": [],
                    "lats": [],
                    "lons": [],
                }
                return fallback

    async def get_status(self) -> dict[str, Any]:
        latest = await self.get_latest(format_type="compact")
        return {
            "status": latest.get("status", "UNAVAILABLE"),
            "is_stale": latest.get("is_stale", True),
            "time_tag": latest.get("time_tag"),
            "observation_time_utc": latest.get("observation_time_utc"),
            "observation_time_vn": latest.get("observation_time_vn"),
            "stats": latest.get("stats", {}),
            "source_name": latest.get("source_name"),
        }


ionosphere_service = IonosphereService()
