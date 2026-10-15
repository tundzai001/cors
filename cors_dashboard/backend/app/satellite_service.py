"""Service to fetch, parse, and cache CelesTrak GNSS satellite TLEs."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import time
from typing import Any, Optional
import httpx

logger = logging.getLogger("app.satellites")

CACHE_TTL_SECONDS = 7200.0  # 2 hours (in compliance with CelesTrak usage policy)

CONSTELLATIONS_CONFIG = {
    "gps": {"group": "GPS-OPS", "prefix": "G", "name": "GPS (USA)", "color": "#22c55e"},
    "galileo": {"group": "GALILEO", "prefix": "E", "name": "Galileo (EU)", "color": "#3b82f6"},
    "beidou": {"group": "BEIDOU", "prefix": "C", "name": "BeiDou (China)", "color": "#f97316"},
    "glonass": {"group": "GLO-OPS", "prefix": "R", "name": "GLONASS (Russia)", "color": "#ef4444"},
    "qzss": {"group": "GNSS", "prefix": "J", "name": "QZSS (Japan)", "color": "#a855f7"},
}

CELESTRAK_BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"


class SatelliteService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached_payload: Optional[dict[str, Any]] = None
        self._last_fetch_monotonic = 0.0
        self._data_dir = os.path.join(os.path.dirname(__file__), "data")
        self._disk_cache_file = os.path.join(self._data_dir, "gnss_satellites_cache.json")

    def _extract_satellite_code(self, constellation: str, name: str, line1: str) -> str:
        prefix = CONSTELLATIONS_CONFIG.get(constellation, {}).get("prefix", "S")
        if constellation == "gps":
            m = re.search(r"PRN\s*(\d+)", name, re.IGNORECASE)
            if m:
                return f"G{int(m.group(1)):02d}"
        elif constellation == "beidou":
            m = re.search(r"C(\d+)", name, re.IGNORECASE)
            if m:
                return f"C{int(m.group(1)):02d}"
        elif constellation == "galileo":
            m = re.search(r"\bGALILEO(?:[\s-]+PRN)?\s+(\d{1,2})\b", name, re.IGNORECASE)
            if m:
                return f"E{int(m.group(1)):02d}"
            m = re.search(r"GSAT\s*0?(\d+)", name, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                return f"E{val:02d}" if val < 100 else f"E{val % 100:02d}"
        elif constellation == "glonass":
            m = re.search(r"\((\d+)\)", name)
            if m:
                return f"R{int(m.group(1)) % 100:02d}"
        elif constellation == "qzss":
            m = re.search(r"QZSS\s*/\s*PRN\s*(\d+)", name, re.IGNORECASE)
            if m:
                return f"J{int(m.group(1)):03d}"

        # Fallback to last 2 digits of NORAD catalog number
        try:
            cat_id = line1[2:7].strip()
            return f"{prefix}{cat_id[-2:]}"
        except Exception:
            return f"{prefix}00"

    def _parse_tle_text(self, text: str, constellation: str) -> list[dict[str, Any]]:
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
        satellites = []
        for i in range(0, len(lines) - 2, 3):
            name = lines[i]
            l1 = lines[i + 1]
            l2 = lines[i + 2]
            if not l1.startswith("1 ") or not l2.startswith("2 "):
                continue
            if constellation == "qzss" and not re.search(r"QZSS\s*/\s*PRN\s*\d+", name, re.IGNORECASE):
                continue

            norad_id = l1[2:7].strip()
            code = self._extract_satellite_code(constellation, name, l1)

            satellites.append({
                "name": name,
                "code": code,
                "norad_id": norad_id,
                "constellation": constellation,
                "line1": l1,
                "line2": l2,
            })
        return satellites

    def _load_disk_cache(self) -> Optional[dict[str, Any]]:
        if os.path.exists(self._disk_cache_file):
            try:
                with open(self._disk_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug("Failed to read disk cache for satellites: %s", e)
        return None

    def _save_disk_cache(self, payload: dict[str, Any]):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._disk_cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            logger.debug("Failed to save disk cache for satellites: %s", e)

    async def get_satellites_tle(self, force_refresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._cached_payload:
                age = now - self._last_fetch_monotonic
                if age < CACHE_TTL_SECONDS:
                    return self._cached_payload

            # Try to load disk cache on cold boot
            if not self._cached_payload and not force_refresh:
                disk_data = self._load_disk_cache()
                cached_constellations = disk_data.get("constellations") if isinstance(disk_data, dict) else None
                if isinstance(cached_constellations, dict) and set(CONSTELLATIONS_CONFIG).issubset(cached_constellations):
                    self._cached_payload = disk_data
                    self._last_fetch_monotonic = now
                    return disk_data

            # Fetch fresh from CelesTrak
            try:
                constellations_data: dict[str, list[dict[str, Any]]] = {}
                total_count = 0

                async with httpx.AsyncClient(verify=False, timeout=12.0) as client:
                    for key, cfg in CONSTELLATIONS_CONFIG.items():
                        url = f"{CELESTRAK_BASE_URL}?GROUP={cfg['group']}&FORMAT=TLE"
                        resp = await client.get(url, headers={"User-Agent": "Aitogy-CORS-System/1.0"})
                        if resp.status_code == 200:
                            sats = self._parse_tle_text(resp.text, key)
                            constellations_data[key] = sats
                            total_count += len(sats)
                        else:
                            logger.warning("CelesTrak returned %s for group %s", resp.status_code, cfg['group'])
                            constellations_data[key] = []

                if total_count > 0:
                    payload = {
                        "status": "OK",
                        "is_stale": False,
                        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "total_satellites": total_count,
                        "counts": {k: len(v) for k, v in constellations_data.items()},
                        "constellations": constellations_data,
                    }
                    self._cached_payload = payload
                    self._last_fetch_monotonic = now
                    self._save_disk_cache(payload)
                    return payload

            except Exception as exc:
                logger.warning("Failed to refresh CelesTrak TLE data: %s", exc)

            # Fallback to existing cache if network failed
            if self._cached_payload:
                stale_payload = dict(self._cached_payload)
                stale_payload["is_stale"] = True
                stale_payload["warning"] = "CelesTrak update temporarily unreachable, using cached TLEs"
                return stale_payload

            # Fallback to disk cache if available
            disk_data = self._load_disk_cache()
            if disk_data:
                disk_data["is_stale"] = True
                constellations = disk_data.setdefault("constellations", {})
                for key in CONSTELLATIONS_CONFIG:
                    constellations.setdefault(key, [])
                counts = disk_data.get("counts")
                if not isinstance(counts, dict):
                    counts = {}
                    disk_data["counts"] = counts
                counts.update({key: len(constellations[key]) for key in CONSTELLATIONS_CONFIG})
                return disk_data

            return {
                "status": "UNAVAILABLE",
                "is_stale": True,
                "total_satellites": 0,
                "counts": {},
                "constellations": {k: [] for k in CONSTELLATIONS_CONFIG},
            }

    async def get_summary(self) -> dict[str, Any]:
        data = await self.get_satellites_tle()
        return {
            "status": data.get("status", "UNAVAILABLE"),
            "is_stale": data.get("is_stale", True),
            "updated_at_utc": data.get("updated_at_utc"),
            "total_satellites": data.get("total_satellites", 0),
            "counts": data.get("counts", {}),
        }


satellite_service = SatelliteService()
