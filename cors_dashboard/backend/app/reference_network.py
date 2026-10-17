"""Public VNGEONET website health; this does not attest individual RTCM streams."""

import asyncio
import time
from html.parser import HTMLParser

import httpx
from fastapi import APIRouter, Response

LOGIN_URL = "https://vngeonet.vn/Account/Index?returnUrl=%2F"
CACHE_SECONDS = 30
MAX_PAGE_BYTES = 1024 * 1024
router = APIRouter()
_cache = None
_cached_at = 0.0
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


async def probe_network():
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
    return {"network": "vngeonet", "status": state, "reason": reason,
            "checked_at": int(time.time()), "source_url": LOGIN_URL,
            "scope": "website", "station_status_available": False}


async def get_network_status():
    global _cache, _cached_at
    # All viewers share one bounded probe per worker, including failed results.
    async with _lock:
        if _cache is None or time.monotonic() - _cached_at >= CACHE_SECONDS:
            _cache = await probe_network()
            _cached_at = time.monotonic()
    return dict(_cache)


@router.get("/api/public/reference-network-status")
async def reference_network_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await get_network_status()
