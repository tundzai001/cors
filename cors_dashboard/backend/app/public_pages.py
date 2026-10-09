"""Public station and blog SEO responses."""

import logging
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import html as html_escape
import json
import re
import unicodedata
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import crud, models
from .database import get_db

logger = logging.getLogger("app.main")
router = APIRouter()


@router.get("/station/{serial}")
async def get_station_seo_page(serial: str, db: AsyncSession = Depends(get_db)):
    """
    ENTERPRISE SEO ENGINE: Renders a high-performance, crawler-optimized page
    with deep JSON-LD structured data for GNSS stations.
    """
    def _station_slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        slug = re.sub(r"[^a-zA-Z0-9._-]+", ".", ascii_text).strip(".")
        slug = re.sub(r"\.{2,}", ".", slug)
        return slug.lower()

    token = (serial or "").strip()
    token_slug = _station_slug(token)

    device = await crud.get_device_by_serial(db, token)
    if not device:
        all_devices = (await db.execute(select(models.Device))).scalars().all()
        for item in all_devices:
            if _station_slug(item.name or item.serial or "") == token_slug:
                device = item
                break
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"

    if not index_path.exists():
        return HTMLResponse(content="<h1>System Error: Frontend missing</h1>", status_code=500)

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        if device:
            # 1. DATA EXTRACTION
            station_name = device.name or token
            status_text = "Online" if device.status == "online" else "Offline"

            # Extract Coordinates
            base_cfg = device.base_config or {}
            coords = base_cfg.get("coords", {})
            lat = coords.get("lat") or base_cfg.get("lat", 0.0)
            lon = coords.get("lon") or base_cfg.get("lon", 0.0)
            alt = coords.get("alt") or base_cfg.get("alt", 0.0)

            # 2. BUILD DYNAMIC SEO TAGS
            title = f"{station_name} GNSS CORS Station | RTK Network Vietnam | Aitogy"
            desc = f"Trạm CORS {station_name} ({serial}) tại {lat}, {lon}. Trạng thái: {status_text}. Theo dõi tín hiệu vệ tinh GPS/GLONASS/BDS và độ trễ RTK thời gian thực."

            # 3. BUILD DEEP JSON-LD (Schema.org)
            json_ld = {
                "@context": "https://schema.org",
                "@type": "Place",
                "name": f"GNSS CORS Station {station_name}",
                "description": desc,
                "identifier": serial,
                "geo": {
                    "@type": "GeoCoordinates",
                    "latitude": lat,
                    "longitude": lon,
                    "elevation": alt
                },
                "additionalType": "https://en.wikipedia.org/wiki/GNSS_augmentation",
                "provider": {
                    "@type": "Organization",
                    "name": "AITOGY .,JSC",
                    "legalName": "Aitogy Infrastructure Technology Joint Stock Company",
                    "url": "https://cors.aitogy.com"
                }
            }
            json_ld_json = json.dumps(json_ld, ensure_ascii=False)
            json_ld_json = json_ld_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            json_ld_script = f'<script type="application/ld+json">{json_ld_json}</script>'

            # 4. SERVER-SIDE INJECTION
            # Inject standard meta
            safe_title = html_escape.escape(title, quote=True)
            safe_desc = html_escape.escape(desc, quote=True)
            html_content = html_content.replace("<title>Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao GNSS/RTK</title>", f"<title>{safe_title}</title>")
            html_content = html_content.replace('name="description" content="Aitogy CORS Control - Hệ thống quản trị mạng lưới trạm định vị vệ tinh (CORS) chuyên nghiệp. Cung cấp giải pháp định vị chính xác cao cho khảo sát và đo đạc."', f'name="description" content="{safe_desc}"')

            # Inject OG Tags
            html_content = html_content.replace('property="og:title" content="Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao"', f'property="og:title" content="{safe_title}"')
            html_content = html_content.replace('property="og:description" content="Quản lý và vận hành mạng lưới trạm CORS Aitogy. Giải pháp định vị chính xác cao hàng đầu."', f'property="og:description" content="{safe_desc}"')

            # Inject JSON-LD before head closing
            html_content = html_content.replace("</head>", f"{json_ld_script}\n</head>")

        return HTMLResponse(content=html_content)
    except Exception as e:
        logging.error(f"❌ Professional SEO Injection Failed: {e}")
        return FileResponse(index_path)

@router.get("/sitemap.xml")
async def get_dynamic_sitemap(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generates a dynamic XML sitemap listing the homepage and all active CORS stations.
    This ensures Google always has an up-to-date list of every station page.
    """
    base_url = "https://cors.aitogy.com"

    def _station_slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        slug = re.sub(r"[^a-zA-Z0-9._-]+", ".", ascii_text).strip(".")
        slug = re.sub(r"\.{2,}", ".", slug)
        return slug.lower()

    # Fetch public station names from DB and build slug URLs (no serial leakage)
    result = await db.execute(select(models.Device.name, models.Device.serial))
    stations = result.all()

    xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    # 1. Main Pages
    for path in ["/", "/network-status", "/coverage", "/blog"]:
        xml_content += f'  <url>\n    <loc>{base_url}{path}</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'

    # 2. Dynamic Station Pages
    for station_name, station_serial in stations:
        station_slug = _station_slug(station_name or station_serial or "")
        if not station_slug:
            continue
        xml_content += f'  <url>\n    <loc>{base_url}/station/{station_slug}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'

    xml_content += f'</urlset>'

    return Response(content=xml_content, media_type="application/xml")

@router.get("/blog/{slug}")
async def get_blog_post_seo(slug: str, request: Request):
    """
    Renders index.html with server-side SEO for individual blog posts.
    Each technical article becomes a landing page for Google Search.
    """
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"

    # Mock data for SEO injection (In a real system, this would come from a Blog DB table)
    blog_metadata = {
        "ntrip-caster-la-gi": {
            "title": "NTRIP Caster là gì? Hướng dẫn toàn tập về giao thức RTK | Aitogy",
            "desc": "Tìm hiểu về NTRIP Caster, vai trò của nó trong mạng lưới CORS và cách kết nối đầu thu GNSS để đạt độ chính xác centimet."
        },
        "huong-dan-ket-noi-cors": {
            "title": "Cách kết nối trạm CORS Aitogy cho Drone và Máy công trình",
            "desc": "Hướng dẫn chi tiết từng bước cấu hình tài khoản NTRIP để nhận tín hiệu hiệu chỉnh RTK từ hệ thống trạm CORS Aitogy."
        }
    }

    meta = blog_metadata.get(slug, {
        "title": "Blog Kỹ thuật GNSS/RTK | Aitogy Knowledge Base",
        "desc": "Kiến thức chuyên sâu về công nghệ định vị vệ tinh, hạ tầng CORS và giải pháp RTK chính xác cao."
    })

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Inject Blog Meta
        html_content = html_content.replace("<title>Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao GNSS/RTK</title>", f"<title>{meta['title']}</title>")
        html_content = html_content.replace('name="description" content="Aitogy CORS Control - Hệ thống quản trị mạng lưới trạm định vị vệ tinh (CORS) chuyên nghiệp. Cung cấp giải pháp định vị chính xác cao cho khảo sát và đo đạc."', f'name="description" content="{meta["desc"]}"')

        # Article Schema
        article_schema = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": meta['title'],
            "description": meta['desc'],
            "author": {"@type": "Organization", "name": "Aitogy"},
            "publisher": {"@type": "Organization", "name": "Aitogy", "logo": "https://cors.aitogy.com/img/aitogy.png"},
            "mainEntityOfPage": {"@type": "WebPage", "@id": f"https://cors.aitogy.com/blog/{slug}"}
        }
        html_content = html_content.replace("</head>", f'<script type="application/ld+json">{json.dumps(article_schema)}</script>\n</head>')

        return HTMLResponse(content=html_content)
    except Exception:
        return FileResponse(index_path)

@router.get("/network-status")
@router.get("/coverage")
@router.get("/blog")
async def get_public_pages():
    """Catch-all for other public SEO pages to serve index.html."""
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    return FileResponse(index_path)
