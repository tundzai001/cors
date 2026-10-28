"""Public station and blog SEO responses."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import json
from pathlib import Path
from urllib.parse import quote
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from . import crud, models
from .database import get_db
from .seo import BLOG_METADATA, page_metadata, station_slug

logger = logging.getLogger("app.main")
router = APIRouter()


@router.get("/station/{serial}")
async def get_station_seo_page(serial: str, db: AsyncSession = Depends(get_db)):
    """
    ENTERPRISE SEO ENGINE: Renders a high-performance, crawler-optimized page
    with deep JSON-LD structured data for GNSS stations.
    """
    token = (serial or "").strip()
    token_slug = station_slug(token)

    device = await crud.get_device_by_serial(db, token)
    if not device:
        all_devices = (await db.execute(select(models.Device))).scalars().all()
        for item in all_devices:
            if station_slug(item.name or item.serial or "") == token_slug:
                device = item
                break
    if device is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy trạm Base")

    slug = station_slug(device.name or device.serial)
    canonical_path = f"/station/{quote(slug or device.serial, safe='')}"
    if serial != (slug or device.serial):
        return RedirectResponse(canonical_path, status_code=308)

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
            title = f"Trạm Base {station_name} | Hệ thống Base Aitogy"
            page_url = f"https://base.aitogy.com{canonical_path}"
            desc = f"Trạm Base {station_name} ({serial}) tại {lat}, {lon}. Trạng thái: {status_text}. Theo dõi tín hiệu vệ tinh GPS/GLONASS/BDS và độ trễ RTK thời gian thực."

            # 3. BUILD DEEP JSON-LD (Schema.org)
            json_ld = {
                "@context": "https://schema.org",
                "@type": "Place",
                "name": f"GNSS Base Station {station_name}",
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
                    "url": "https://base.aitogy.com"
                }
            }
            json_ld_json = json.dumps(json_ld, ensure_ascii=False)
            json_ld_json = json_ld_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            json_ld_script = f'<script type="application/ld+json">{json_ld_json}</script>'

            html_content = page_metadata(
                html_content, title=title, description=desc, url=page_url
            )

            # Inject JSON-LD before head closing
            html_content = html_content.replace("</head>", f"{json_ld_script}\n</head>")

        return HTMLResponse(content=html_content)
    except Exception as e:
        logging.error(f"❌ Professional SEO Injection Failed: {e}")
        return FileResponse(index_path)

@router.get("/sitemap.xml")
async def get_dynamic_sitemap(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generates a dynamic XML sitemap listing the homepage and all active Base stations.
    This ensures Google always has an up-to-date list of every station page.
    """
    base_url = "https://base.aitogy.com"

    # Fetch public station names from DB and build slug URLs (no serial leakage)
    result = await db.execute(select(models.Device.name, models.Device.serial))
    stations = result.all()

    xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    # 1. Main Pages
    for path in ["/", "/blog"]:
        xml_content += f'  <url>\n    <loc>{base_url}{path}</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'

    # Published articles only; unknown slugs must not enter the index.
    for slug in BLOG_METADATA:
        xml_content += f'  <url>\n    <loc>{base_url}/blog/{slug}</loc>\n  </url>\n'

    # 2. Dynamic Station Pages
    slugs = sorted({station_slug(name or serial or "") for name, serial in stations} - {""})
    for slug in slugs:
        xml_content += f'  <url>\n    <loc>{base_url}/station/{slug}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'

    xml_content += f'</urlset>'

    return Response(content=xml_content, media_type="application/xml")

@router.get("/blog/{slug}")
async def get_blog_post_seo(slug: str, request: Request):
    """
    Renders index.html with server-side SEO for individual blog posts.
    Each technical article becomes a landing page for Google Search.
    """
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"

    meta = BLOG_METADATA.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài viết")

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        html_content = page_metadata(
            html_content, title=meta["title"], description=meta["desc"],
            url=f"https://base.aitogy.com/blog/{quote(slug, safe='')}"
        )

        # Article Schema
        article_schema = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": meta["title"].split(" | ")[0],
            "inLanguage": "vi-VN",
            "isPartOf": {"@id": "https://base.aitogy.com/#website"},
            "description": meta['desc'],
            "author": {"@type": "Organization", "name": "Aitogy"},
            "publisher": {"@type": "Organization", "name": "Aitogy", "logo": "https://base.aitogy.com/assets/img/aitogy.png"},
            "mainEntityOfPage": {"@type": "WebPage", "@id": f"https://base.aitogy.com/blog/{slug}"}
        }
        breadcrumb_schema = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Bài viết", "item": "https://base.aitogy.com/blog"},
                {"@type": "ListItem", "position": 2, "name": article_schema["headline"],
                 "item": article_schema["mainEntityOfPage"]["@id"]},
            ],
        }
        schemas = json.dumps([article_schema, breadcrumb_schema], ensure_ascii=False)
        schemas = schemas.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        html_content = html_content.replace("</head>", f'<script type="application/ld+json">{schemas}</script>\n</head>')

        return HTMLResponse(content=html_content)
    except Exception:
        return FileResponse(index_path)

@router.get("/network-status")
@router.get("/coverage")
@router.get("/blog")
async def get_public_pages(request: Request):
    """Keep homepage aliases canonical; give the knowledge index its own URL."""
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    if request.url.path == "/blog":
        return HTMLResponse(page_metadata(
            index_path.read_text(encoding="utf-8"),
            title="Kiến thức GNSS/RTK | Hệ thống Base Aitogy",
            description="Hướng dẫn kết nối, vận hành trạm Base và kiến thức định vị GNSS/RTK từ Aitogy.",
            url="https://base.aitogy.com/blog",
        ))
    return FileResponse(index_path)
