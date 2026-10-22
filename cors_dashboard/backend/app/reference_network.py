"""Configurable website/RTCM health checks with shared upstream cooldowns."""

import asyncio
import base64
import hashlib
import logging
import sqlite3
import time
from html.parser import HTMLParser

import httpx
from fastapi import APIRouter, Response
from .runtime_config import _get_env_preferring_dotenv
from .rtcm_signal_decoder import crc24q
from .reference_network_cache import claim_probe, save_probe
from .reference_network_settings import get_check_settings

logger = logging.getLogger(__name__)


LOGIN_URL = "https://vngeonet.vn/Account/Index?returnUrl=%2F"
CACHE_SECONDS = 30
MAX_PAGE_BYTES = 1024 * 1024
RTCM_PROBE_SECONDS = 6
MAX_STREAM_BYTES = 65536
router = APIRouter()
_cache = None
_cached_at = 0.0
_cached_settings = None
_lock = asyncio.Lock()


class PageSignals(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.title = []
        self.text = []
        self.in_title = False
        self.ignored = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"].lower())
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.ignored:
            self.text.append(data)


def classify_page(html):
    page = PageSignals(html)
    title = " ".join(page.title).strip().lower()
    text = " ".join(page.text).lower()
    if ("handlederrormessage" in page.ids
            or any(word in title for word in ("error", "unavailable", "lỗi"))
            or "sorry, an error occurred while processing your request" in text):
        return "offline"
    return "online" if {"login-form", "username", "password"} <= page.ids else "offline"


async def _read_page(client, url):
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError("Reference page exceeds size limit")
        return body.decode("utf-8", errors="replace")


def _probe_gga():
    # A reference rover position in Hanoi, inside the VRS.105M3 coverage.
    body = f"GPGGA,{time.strftime('%H%M%S', time.gmtime())}.00,2101.7100,N,10551.2520,E,1,12,1.0,10.0,M,-30.0,M,,"
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}\r\n".encode("ascii")


def _has_rtcm(buffer):
    """Keep partial packets across reads; discard headers, noise and bad CRCs."""
    while len(buffer) >= 3:
        start = buffer.find(b"\xd3")
        if start < 0:
            buffer.clear()
            break
        del buffer[:start]
        if len(buffer) < 3:
            break
        if buffer[1] & 0xFC:
            del buffer[0]
            continue
        length = ((buffer[1] & 3) << 8) | buffer[2]
        size = length + 6
        if len(buffer) < size:
            break
        if length >= 2 and crc24q(buffer[:size - 3]) == int.from_bytes(buffer[size - 3:size], "big"):
            return True
        del buffer[0]
    return False


async def probe_rtcm():
    user = _get_env_preferring_dotenv("CORS_CUC_NTRIP_USER", "")
    password = _get_env_preferring_dotenv("CORS_CUC_NTRIP_PASSWORD", "")
    if not user or not password:
        return "unknown", "Chưa cấu hình tài khoản kiểm tra dữ liệu Cục"
    writer = None
    accepted = False
    try:
        # Bound the entire connection, login and stream read, including slow peers.
        async with asyncio.timeout(RTCM_PROBE_SECONDS):
            host = _get_env_preferring_dotenv("CORS_CUC_NTRIP_HOST", "14.238.1.125")
            port = int(_get_env_preferring_dotenv("CORS_CUC_NTRIP_PORT", "2101"))
            mountpoint = _get_env_preferring_dotenv("CORS_CUC_NTRIP_MOUNTPOINT", "VRS.105M3")
            if any(c in host + mountpoint for c in "\r\n"):
                raise ValueError("Invalid NTRIP endpoint")
            reader, writer = await asyncio.open_connection(host, port, limit=4096)
            auth = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            writer.write((f"GET /{mountpoint} HTTP/1.0\r\nHost: {host}:{port}\r\n"
                          f"User-Agent: NTRIP CORS-Health/1.0\r\nAuthorization: Basic {auth}\r\n\r\n").encode("ascii"))
            await writer.drain()
            status = (await reader.readline()).strip().split()
            if (len(status) >= 2 and status[0] in (b"ICY", b"HTTP/1.0", b"HTTP/1.1")
                    and status[1] in (b"401", b"403")):
                return "unknown", "Lỗi xác thực hoặc quyền truy cập NTRIP; chưa xác minh được dữ liệu Cục"
            if len(status) < 2 or status[0] not in (b"ICY", b"HTTP/1.0", b"HTTP/1.1") or status[1] != b"200":
                return "offline", "Nguồn dữ liệu Cục từ chối kết nối NTRIP"
            accepted = True
            # Some NTRIP v1 casters send only the ICY line, without a blank line.
            writer.write(_probe_gga())
            await writer.drain()
            buffer = bytearray()
            received = 0
            while received < MAX_STREAM_BYTES:
                chunk = await reader.read(min(4096, MAX_STREAM_BYTES - received))
                if not chunk:
                    break
                received += len(chunk)
                buffer.extend(chunk)
                if _has_rtcm(buffer):
                    # Return through finally immediately; never keep a valid stream open.
                    return "online", "Nguồn dữ liệu Cục có RTCM hợp lệ sau khi gửi GGA"
            return "offline", "Nguồn dữ liệu Cục không trả RTCM hợp lệ sau khi gửi GGA"
    except TimeoutError:
        return ("offline", "Nguồn dữ liệu Cục không trả RTCM trong thời gian kiểm tra") if accepted else (
            "unknown", "Không kết nối được nguồn dữ liệu Cục trong thời gian kiểm tra")
    except (OSError, ValueError):
        return "unknown", "Không kiểm tra được nguồn dữ liệu Cục"
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            except (OSError, TimeoutError):
                pass


async def _probe_website():
    state, reason = "unknown", "Không kiểm tra được trang dịch vụ"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, max_redirects=3) as client:
            state = classify_page(await _read_page(client, LOGIN_URL))
        reason = {"online": "Trang dịch vụ Cục có form đăng nhập bình thường",
                  "offline": "Trang dịch vụ Cục báo lỗi hoặc không có form đăng nhập",
                  "unknown": "Nội dung trang Cục chưa xác định"}[state]
    except httpx.HTTPStatusError:
        state, reason = "offline", "Trang dịch vụ Cục trả lỗi HTTP"
    except (httpx.HTTPError, ValueError):
        pass
    return state, reason


async def _scheduled_probe(key, interval, probe):
    try:
        cached, reservation = await asyncio.to_thread(claim_probe, key, interval, time.time())
    except (OSError, sqlite3.Error, ValueError):
        # Fail closed: a broken cache must not cause repeated upstream connections.
        logger.warning("CORS Cục polling cache unavailable; skipping probe")
        return {"status": "unknown", "reason": "Chưa cập nhật được trạng thái Cục", "checked_at": 0}
    if reservation is None:
        return cached or {"status": "unknown", "reason": "Đang chờ kết quả kiểm tra Cục", "checked_at": 0}
    state, reason = await probe()
    result = {"status": state, "reason": reason, "checked_at": int(time.time())}
    try:
        await asyncio.to_thread(save_probe, key, reservation, result)
    except (OSError, sqlite3.Error):
        logger.warning("CORS Cục polling result could not be saved")
    return result


def _rtcm_cache_key():
    # Invalidate old results if the endpoint or credentials change, without storing secrets.
    values = [_get_env_preferring_dotenv("CORS_CUC_NTRIP_" + name, default) for name, default in (
        ("HOST", "14.238.1.125"), ("PORT", "2101"), ("MOUNTPOINT", "VRS.105M3"), ("USER", ""), ("PASSWORD", ""))]
    return "rtcm:" + hashlib.sha256("\0".join(values).encode()).hexdigest()


def _current_result(result, interval):
    if not result["checked_at"] or not 0 <= time.time() - result["checked_at"] < interval + 60:
        return {"status": "unknown", "reason": "Kết quả kiểm tra Cục chưa cập nhật", "checked_at": 0}
    return result


async def probe_network(config=None):
    config = config or get_check_settings()
    web_seconds = config["web_check_interval_seconds"]
    rtcm_seconds = config["rtcm_check_interval_seconds"]
    enabled = config["rtcm_check_enabled"]
    website = _current_result(await _scheduled_probe("website", web_seconds, _probe_website), web_seconds)
    rtcm = {"status": "not_checked", "checked_at": 0}
    selected, interval = website, web_seconds
    if enabled:
        rtcm = _current_result(await _scheduled_probe(_rtcm_cache_key(), rtcm_seconds, probe_rtcm), rtcm_seconds)
        selected, interval = rtcm, rtcm_seconds
    return {"network": "vngeonet", "status": selected["status"], "reason": selected["reason"],
            "checked_at": selected["checked_at"], "source_url": LOGIN_URL,
            "valid_until": selected["checked_at"] + interval + 60 if selected["checked_at"] else 0,
            "scope": "ntrip" if enabled else "website",
            "website_status": website["status"], "website_checked_at": website["checked_at"],
            "rtcm_status": rtcm["status"], "rtcm_checked_at": rtcm["checked_at"],
            **config,
            "station_status_available": False}


async def get_network_status():
    global _cache, _cached_at, _cached_settings
    # This local cache limits disk reads; persistent reservations bound upstream probes.
    async with _lock:
        config = get_check_settings()
        cache_seconds = min(CACHE_SECONDS, config["web_check_interval_seconds"], config["rtcm_check_interval_seconds"])
        if _cache is None or config != _cached_settings or time.monotonic() - _cached_at >= cache_seconds:
            _cache = await probe_network(config)
            _cached_at = time.monotonic()
            _cached_settings = dict(config)
    return dict(_cache)


@router.get("/api/public/reference-network-status")
async def reference_network_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await get_network_status()
