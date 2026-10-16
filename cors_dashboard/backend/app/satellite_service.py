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
IGS_METADATA_URL = "https://files.igs.org/pub/station/general/igs_satellite_metadata.snx"


class SatelliteService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached_payload: Optional[dict[str, Any]] = None
        self._last_fetch_monotonic = 0.0
        self._data_dir = os.path.join(os.path.dirname(__file__), "data")
        self._disk_cache_file = os.path.join(self._data_dir, "gnss_satellites_cache.json")
        with open(os.path.join(self._data_dir, "gnss_identifiers.snx"), encoding="utf-8") as source:
            self._identifiers = self._parse_identifiers(source.read())
        self._identifiers_cache_file = os.path.join(self._data_dir, "gnss_identifiers_cache.json")
        try:
            with open(self._identifiers_cache_file, encoding="utf-8") as source:
                identifiers = self._parse_identifiers(json.load(source)["sinex"])
            if identifiers:
                self._identifiers = identifiers
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def _parse_identifiers(text: str) -> dict[str, list[tuple[str, str, str]]]:
        """Join IGS spacecraft identifiers with time-bounded PRN/slot assignments."""
        if not isinstance(text, str):
            return {}
        norad_by_svn = {}
        assignments = []
        block = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("+"):
                block = line[1:]
            elif line.startswith("-"):
                block = ""
            elif line and not line.startswith("*"):
                fields = line.split()
                if block == "SATELLITE/IDENTIFIER" and len(fields) >= 3:
                    norad_by_svn[fields[0]] = fields[2].lstrip("0")
                elif block == "SATELLITE/PRN" and len(fields) >= 4:
                    assignments.append(fields[:4])
        result = {}
        for svn, start, end, code in assignments:
            norad = norad_by_svn.get(svn)
            if norad and re.fullmatch(r"[GRECJI]\d{2,3}", code):
                result.setdefault(norad, []).append((start, end, code))
        return result

    def _normalize_codes(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Re-identify old disk/in-memory caches too; TLE names can retain former PRNs.
        for constellation, satellites in payload.get("constellations", {}).items():
            for sat in satellites:
                sat["code"] = self._extract_satellite_code(constellation, sat["name"], sat["line1"])
        return payload

    def _extract_satellite_code(self, constellation: str, name: str, line1: str) -> str:
        prefix = CONSTELLATIONS_CONFIG.get(constellation, {}).get("prefix", "S")
        assignments = self._identifiers.get(line1[2:7].strip().lstrip("0"))
        if assignments:
            now = datetime.now(timezone.utc)
            epoch = now.strftime("%Y:%j:") + f"{now.hour * 3600 + now.minute * 60 + now.second:05d}"
            for start, end, code in reversed(assignments):
                if start <= epoch and (end == "0000:000:00000" or epoch < end):
                    if code.startswith(prefix):
                        if constellation == "qzss" and int(code[1:]) < 100:
                            return f"J{int(code[1:]) + 192:03d}"
                        return code
            return f"{prefix}?"
        if constellation == "gps":
            m = re.search(r"PRN\s*(\d+)", name, re.IGNORECASE)
            if m:
                return f"G{int(m.group(1)):02d}"
        elif constellation == "beidou":
            m = re.search(r"C(\d+)", name, re.IGNORECASE)
            if m:
                return f"C{int(m.group(1)):02d}"
        elif constellation == "qzss":
            m = re.search(r"QZSS\s*/\s*PRN\s*(\d+)", name, re.IGNORECASE)
            if m:
                return f"J{int(m.group(1)):03d}"

        # Unknown identity must never accidentally match a live RTCM PRN.
        return f"{prefix}?"

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
                    return self._normalize_codes(self._cached_payload)

            # Try to load disk cache on cold boot
            if not self._cached_payload and not force_refresh:
                disk_data = self._load_disk_cache()
                cached_constellations = disk_data.get("constellations") if isinstance(disk_data, dict) else None
                if isinstance(cached_constellations, dict) and set(CONSTELLATIONS_CONFIG).issubset(cached_constellations):
                    self._cached_payload = disk_data
                    try:
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(disk_data["updated_at_utc"])).total_seconds()
                    except (KeyError, ValueError, TypeError):
                        age = CACHE_TTL_SECONDS
                    if 0 <= age < CACHE_TTL_SECONDS:
                        self._last_fetch_monotonic = now - age
                        return self._normalize_codes(disk_data)

            # Fetch fresh from CelesTrak
            try:
                constellations_data: dict[str, list[dict[str, Any]]] = {}
                total_count = 0

                async with httpx.AsyncClient(verify=False, timeout=12.0) as client:
                    try:
                        metadata = await client.get(IGS_METADATA_URL)
                        metadata.raise_for_status()
                        identifiers = self._parse_identifiers(metadata.text)
                        if identifiers:
                            self._identifiers = identifiers
                            try:
                                with open(self._identifiers_cache_file, "w", encoding="utf-8") as cache:
                                    json.dump({"sinex": metadata.text}, cache)
                            except OSError as exc:
                                logger.debug("Could not cache IGS identifiers: %s", exc)
                    except Exception as exc:
                        logger.warning("IGS identifiers unavailable, using bundled/previous assignments: %s", exc)
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
                return self._normalize_codes(stale_payload)

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
                return self._normalize_codes(disk_data)

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
