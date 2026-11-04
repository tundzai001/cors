"""Exercise public SEO responses without connecting to a database."""

import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import public_pages
from app.frontend_routes import router as frontend_router
from app.database import get_db
from app.seo import BLOG_METADATA
from tests.test_seo import Head


class PublicSeoTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(public_pages.router)
        app.include_router(frontend_router)
        self.db = AsyncMock()
        self.db.execute.return_value = Mock()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def test_every_published_article_has_own_metadata(self):
        for slug, meta in BLOG_METADATA.items():
            with self.subTest(slug=slug):
                response = self.client.get(f"/blog/{slug}")
                self.assertEqual(response.status_code, 200)
                head = Head(response.text)
                self.assertEqual(head.meta["description"], meta["desc"])
                self.assertEqual(head.meta["og:title"], meta["title"])
                self.assertEqual(head.canonicals, [f"https://base.aitogy.com/blog/{slug}"])
                schemas = [json.loads(value) for value in re.findall(
                    r'<script type="application/ld\+json">(.*?)</script>', response.text, re.S
                )]
                article, breadcrumb = schemas[-1]
                self.assertEqual(article["@type"], "TechArticle")
                self.assertEqual(breadcrumb["@type"], "BreadcrumbList")
                self.assertEqual(breadcrumb["itemListElement"][-1]["item"], head.canonicals[0])
                self.assertEqual(breadcrumb["itemListElement"][-1]["name"], article["headline"])

    def test_missing_article_and_station_return_404(self):
        self.assertEqual(self.client.get("/blog/not-a-published-article").status_code, 404)
        for path in ("/blog/missing/nested", "/station/missing/nested", "/station/"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.db.execute.return_value.scalars.return_value.all.return_value = []
        with patch.object(public_pages.crud, "get_device_by_serial", AsyncMock(return_value=None)):
            self.assertEqual(self.client.get("/station/not-a-real-station").status_code, 404)

    def test_station_canonical_and_sitemap_use_same_name(self):
        device = SimpleNamespace(name="Hà Nội", serial="internal-id", status="online", base_config={})
        with patch.object(public_pages.crud, "get_device_by_serial", AsyncMock(return_value=device)):
            response = self.client.get("/station/internal-id")
        expected = "https://base.aitogy.com/station/ha.noi"
        self.assertEqual(response.history[0].status_code, 308)
        self.assertEqual(response.history[0].headers["location"], "/station/ha.noi")
        self.assertEqual(Head(response.text).canonicals, [expected])
        self.db.execute.return_value.all.return_value = [(device.name, device.serial), (device.name, "another-id"), ("", "")]
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.text)
        urls = [node.text for node in root.findall("{*}url/{*}loc")]
        self.assertIn(expected, urls)
        self.assertEqual(len(urls), len(set(urls)))
        for slug in BLOG_METADATA:
            self.assertIn(f"https://base.aitogy.com/blog/{slug}", urls)
        self.assertNotIn("https://base.aitogy.com/coverage", urls)
        self.assertNotIn("https://base.aitogy.com/network-status", urls)

    def test_seo_does_not_execute_markup_from_station_or_blog_path(self):
        payload = '</script><script>alert(1)</script>'
        device = SimpleNamespace(name=payload, serial="internal-id", status="online", base_config={})
        with patch.object(public_pages.crud, "get_device_by_serial", AsyncMock(return_value=device)):
            response = self.client.get("/station/script.script.alert.1.script")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(payload, response.text)
        schema = re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.text, re.S)[-1]
        self.assertEqual(json.loads(schema)["name"], f"GNSS Base Station {payload}")

        response = self.client.get(f"/blog/{quote(payload, safe='')}")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(payload, response.text)

    def test_index_redirect_preserves_map_query(self):
        for query in ("", "?view=map", "?view=map&station=ha.noi"):
            with self.subTest(query=query):
                response = self.client.get(f"/index.html{query}", follow_redirects=False)
                self.assertEqual(response.status_code, 308)
                self.assertEqual(response.headers["location"], f"/{query}")


if __name__ == "__main__":
    unittest.main()
