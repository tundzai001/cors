"""Service to fetch, parse, and cache CelesTrak GNSS satellite TLEs."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Optional
import httpx

from .gnss_status_sources import QZSS_STATUS_URL, parse_qzss_status

TLE_EPOCH_FORMAT = "%Y:%j:"

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

SUPPLEMENTAL_TLE: dict[str, str] = {
    "galileo": (
        "GSAT0220 (GALILEO 24)\n"
        "1 43567U 18060D   26259.06046683 -.00000101  00000-0  00000-0 0  9998\n"
        "2 43567  57.1352 339.3320 0003721 269.4250  90.5848  1.70476412 50724\n"
        "GSAT0213 (GALILEO 17)\n"
        "1 41861U 16069C   26258.30017772  .00000003  00000-0  00000-0 0 09995\n"
        "2 41861  55.5571  98.5941 0002004 331.4312  28.6313  1.70474479 61057"
    ),
}


class SatelliteService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached_payload: Optional[dict[str, Any]] = None
        self._last_fetch_monotonic = 0.0
        self._data_dir = os.path.join(os.path.dirname(__file__), "data")
        self._disk_cache_file = os.path.join(self._data_dir, "gnss_satellites_cache.json")
        with open(os.path.join(self._data_dir, "gnss_identifiers.snx"), encoding="utf-8") as source:
            self._identifier_text = source.read()
            self._identifiers = self._parse_identifiers(self._identifier_text)
        self._status_cache_file = os.path.join(self._data_dir, "gnss_status_cache.json")
        self._status_snapshot = {}
        for filename in ("gnss_status_snapshot.json", "gnss_status_cache.json"):
            try:
                with open(os.path.join(self._data_dir, filename), encoding="utf-8") as source:
                    snapshot = json.load(source)
                if snapshot.get("source") == QZSS_STATUS_URL and isinstance(snapshot.get("records"), dict):
                    if snapshot.get("published_at_utc", "") >= self._status_snapshot.get("published_at_utc", ""):
                        self._status_snapshot = snapshot
            except (OSError, ValueError, TypeError):
                pass
        self._identifiers_cache_file = os.path.join(self._data_dir, "gnss_identifiers_cache.json")
        try:
            with open(self._identifiers_cache_file, encoding="utf-8") as source:
                identifier_text = json.load(source)["sinex"]
                identifiers = self._parse_identifiers(identifier_text)
            if identifiers:
                self._identifiers = identifiers
                self._identifier_text = identifier_text
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def _parse_identifier_records(text: str):
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
        return norad_by_svn, assignments

    @staticmethod
    def _join_satellite_assignments(norad_by_svn, assignments):
        result = {}
        for svn, start, end, code in assignments:
            norad = norad_by_svn.get(svn)
            if norad and re.fullmatch(r"[GRECJI]\d{2,3}", code):
                result.setdefault(norad, []).append((start, end, code))
        return result

    @staticmethod
    def _parse_identifiers(text: str) -> dict[str, list[tuple[str, str, str]]]:
        """Join IGS spacecraft identifiers with time-bounded PRN/slot assignments."""
        if not isinstance(text, str):
            return {}
        norad_by_svn, assignments = SatelliteService._parse_identifier_records(text)
        return SatelliteService._join_satellite_assignments(norad_by_svn, assignments)

    def _normalize_codes(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Merge supplemental operational satellites when constellation data is present.
        constellations = payload.get("constellations", {})
        self._merge_supplemental_constellations(payload, constellations)

        # Keep operational PRNs distinct from historical TLE names in every cache path.
        rows = [(constellation, sat) for constellation, satellites in constellations.items() for sat in satellites]
        owners = self._current_assignment_owners()
        self._assign_tle_codes(rows, owners)
        self._apply_status_snapshot(rows, owners)
        return payload

    def _merge_supplemental_constellations(self, payload, constellations):
        for key, sup_tle in SUPPLEMENTAL_TLE.items():
            self._merge_constellation_supplement(payload, constellations, key, sup_tle)

    def _merge_constellation_supplement(self, payload, constellations, key, sup_tle):
        satellites = constellations.get(key)
        if not isinstance(satellites, list) or not satellites:
            return
        existing_norads = {
            str(sat.get("norad_id", sat.get("line1", "")[2:7])).strip().lstrip("0")
            for sat in satellites
        }
        for supplemental in self._parse_tle_text(sup_tle, key):
            norad = str(supplemental.get("norad_id", "")).strip().lstrip("0")
            if not norad or norad in existing_norads:
                continue
            satellites.append(supplemental)
            existing_norads.add(norad)
        self._update_constellation_counts(payload, constellations, key, satellites)

    @staticmethod
    def _update_constellation_counts(payload, constellations, key, satellites):
        counts = payload.get("counts")
        if isinstance(counts, dict) and key in counts:
            counts[key] = len(satellites)
        if "total_satellites" in payload:
            payload["total_satellites"] = sum(
                len(items) for items in constellations.values() if isinstance(items, list)
            )

    def _current_assignment_owners(self):
        owners = {}
        now = datetime.now(timezone.utc)
        epoch = now.strftime(TLE_EPOCH_FORMAT) + f"{now.hour * 3600 + now.minute * 60 + now.second:05d}"
        for norad, assignments in self._identifiers.items():
            for start, end, code in assignments:
                if start <= epoch and (end == "0000:000:00000" or epoch < end):
                    canonical = f"J{int(code[1:]) + 192:03d}" if code.startswith("J") and int(code[1:]) < 100 else code
                    owners.setdefault(canonical, set()).add(norad)

        return owners

    def _assign_tle_codes(self, rows, owners):
        for constellation, sat in rows:
            sat["code"] = self._extract_satellite_code(constellation, sat["name"], sat["line1"])
            if "?" not in sat["code"]:
                owners.setdefault(sat["code"], set()).add(str(sat.get("norad_id", sat["line1"][2:7].strip())))

    def _apply_status_snapshot(self, rows, owners):
        for constellation, sat in rows:
            self._apply_satellite_status(constellation, sat, owners)

    def _apply_satellite_status(self, constellation, sat, owners):
        norad = str(sat.get("norad_id", sat["line1"][2:7].strip())).lstrip("0")
        for field in ("status_source", "status_as_of_utc", "operational_status"):
            sat.pop(field, None)
        known = "?" not in sat["code"]
        sat["identity_source"] = IGS_METADATA_URL if norad in self._identifiers else "CelesTrak TLE name"
        sat["identity_status"] = "assigned" if known else "unconfirmed"
        record = self._status_snapshot.get("records", {}).get(norad)
        if not record:
            return
        published = self._status_snapshot.get("published_at_utc", "")
        try:
            date = datetime.fromisoformat(published)
        except (ValueError, TypeError):
            return
        if date.tzinfo is None:
            return
        epoch = date.strftime(TLE_EPOCH_FORMAT) + f"{date.hour * 3600 + date.minute * 60 + date.second:05d}"
        if self._snapshot_is_superseded(norad, date, epoch):
            return
        self._record_satellite_status(constellation, sat, record, published)
        self._apply_unconfirmed_status_code(constellation, sat, norad, record, date, owners)

    def _snapshot_is_superseded(self, norad, date, epoch):
        boundaries = [
            boundary
            for start, end, _ in self._identifiers.get(norad, [])
            for boundary in (start, end)
            if boundary != "0000:000:00000"
        ]
        return date > datetime.now(timezone.utc) or (boundaries and max(boundaries) > epoch)

    def _record_satellite_status(self, constellation, sat, record, published):
        sat["status_source"] = self._status_snapshot["source"]
        sat["status_as_of_utc"] = published
        sat["operational_status"] = record["status"]
        if record["status"] != "retired":
            return
        sat["code"] = CONSTELLATIONS_CONFIG[constellation]["prefix"] + "?"
        sat["identity_status"] = "retired"
        sat["identity_source"] = self._status_snapshot["source"]

    def _apply_unconfirmed_status_code(self, constellation, sat, norad, record, date, owners):
        known = "?" not in sat["code"]
        candidate = record.get("code")
        age = (datetime.now(timezone.utc) - date).total_seconds()
        eligible = (
            not known
            and candidate
            and record["status"] != "retired"
            and candidate.startswith(CONSTELLATIONS_CONFIG[constellation]["prefix"])
            and 0 <= age <= 90 * 86400
            and not (owners.get(candidate, set()) - {norad})
        )
        if not eligible:
            return
        sat["code"] = candidate
        sat["identity_status"] = "assigned"
        sat["identity_source"] = self._status_snapshot["source"]
        owners.setdefault(candidate, set()).add(norad)

    async def _refresh_independent_status(self, client):
        try:
            response = await client.get(QZSS_STATUS_URL)
            response.raise_for_status()
            snapshot = parse_qzss_status(response.text, self._identifier_text)
            if snapshot["published_at_utc"] >= self._status_snapshot.get("published_at_utc", ""):
                self._status_snapshot = snapshot
                try:
                    await asyncio.to_thread(
                        Path(self._status_cache_file).write_text, json.dumps(snapshot), encoding="utf-8"
                    )
                except OSError as exc:
                    logger.debug("Could not cache independent GNSS status: %s", exc)
        except Exception as exc:
            logger.warning("Cabinet Office status unavailable, retaining dated snapshot: %s", exc)

    @staticmethod
    def _assigned_satellite_code(assignments, prefix, constellation):
        now = datetime.now(timezone.utc)
        epoch = now.strftime(TLE_EPOCH_FORMAT) + f"{now.hour * 3600 + now.minute * 60 + now.second:05d}"
        for start, end, code in reversed(assignments):
            if start <= epoch and (end == "0000:000:00000" or epoch < end) and code.startswith(prefix):
                if constellation == "qzss" and int(code[1:]) < 100:
                    return f"J{int(code[1:]) + 192:03d}"
                return code
        return None

    @staticmethod
    def _fallback_satellite_code(constellation, name, prefix):
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

    def _extract_satellite_code(self, constellation: str, name: str, line1: str) -> str:
        prefix = CONSTELLATIONS_CONFIG.get(constellation, {}).get("prefix", "S")
        assignments = self._identifiers.get(line1[2:7].strip().lstrip("0"))
        if assignments:
            code = self._assigned_satellite_code(assignments, prefix, constellation)
            return code if code is not None else f"{prefix}?"
        return self._fallback_satellite_code(constellation, name, prefix)

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

    def _fresh_memory_cache(self, now, force_refresh):
        if not force_refresh and self._cached_payload:
            if now - self._last_fetch_monotonic < CACHE_TTL_SECONDS:
                return self._normalize_codes(self._cached_payload)
        return None

    async def _fresh_disk_cache(self, now, force_refresh):
        if self._cached_payload or force_refresh:
            return None
        disk_data = await asyncio.to_thread(self._load_disk_cache)
        constellations = disk_data.get("constellations") if isinstance(disk_data, dict) else None
        if not isinstance(constellations, dict) or not set(CONSTELLATIONS_CONFIG).issubset(constellations):
            return None
        self._cached_payload = disk_data
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(disk_data["updated_at_utc"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = CACHE_TTL_SECONDS
        if 0 <= age < CACHE_TTL_SECONDS:
            self._last_fetch_monotonic = now - age
            return self._normalize_codes(disk_data)
        return None

    async def _refresh_igs_identifiers(self, client):
        try:
            metadata = await client.get(IGS_METADATA_URL)
            metadata.raise_for_status()
            identifiers = self._parse_identifiers(metadata.text)
            if identifiers:
                self._identifiers = identifiers
                self._identifier_text = metadata.text
                try:
                    await asyncio.to_thread(
                        Path(self._identifiers_cache_file).write_text,
                        json.dumps({"sinex": metadata.text}),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    logger.debug("Could not cache IGS identifiers: %s", exc)
        except Exception as exc:
            logger.warning("IGS identifiers unavailable, using bundled/previous assignments: %s", exc)

    async def _fetch_constellation_data(self, client):
        data = {}
        total_count = 0
        for key, config in CONSTELLATIONS_CONFIG.items():
            url = f"{CELESTRAK_BASE_URL}?GROUP={config['group']}&FORMAT=TLE"
            response = await client.get(url, headers={"User-Agent": "Aitogy-CORS-System/1.0"})
            if response.status_code == 200:
                data[key] = self._parse_tle_text(response.text, key)
                total_count += len(data[key])
            else:
                logger.warning("CelesTrak returned %s for group %s", response.status_code, config['group'])
                data[key] = []
        return data, total_count

    async def _refresh_payload(self, now):
        async with httpx.AsyncClient(timeout=12.0) as client:
            await self._refresh_igs_identifiers(client)
            await self._refresh_independent_status(client)
            constellations, total_count = await self._fetch_constellation_data(client)
        if not total_count:
            return None
        payload = {
            "status": "OK",
            "is_stale": False,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total_satellites": total_count,
            "counts": {key: len(value) for key, value in constellations.items()},
            "constellations": constellations,
        }
        payload = self._normalize_codes(payload)
        self._cached_payload = payload
        self._last_fetch_monotonic = now
        await asyncio.to_thread(self._save_disk_cache, payload)
        return payload

    def _fallback_payload(self):
        if self._cached_payload:
            stale_payload = dict(self._cached_payload)
            stale_payload["is_stale"] = True
            stale_payload["warning"] = "CelesTrak update temporarily unreachable, using cached TLEs"
            return self._normalize_codes(stale_payload)
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
            "status": "UNAVAILABLE", "is_stale": True, "total_satellites": 0,
            "counts": {}, "constellations": {key: [] for key in CONSTELLATIONS_CONFIG},
        }

    async def get_satellites_tle(self, force_refresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            cached = self._fresh_memory_cache(now, force_refresh)
            if cached is not None:
                return cached
            cached = await self._fresh_disk_cache(now, force_refresh)
            if cached is not None:
                return cached
            try:
                payload = await self._refresh_payload(now)
                if payload is not None:
                    return payload
            except Exception as exc:
                logger.warning("Failed to refresh CelesTrak TLE data: %s", exc)
            return self._fallback_payload()

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
