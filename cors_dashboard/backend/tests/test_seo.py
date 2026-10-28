"""Public metadata stays page-specific as homepage branding changes."""

import json
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from app.seo import page_metadata

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class Head(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.meta = {}
        self.canonicals = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "meta":
            self.meta[attrs.get("name", attrs.get("property"))] = attrs.get("content")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs["href"])


class SeoTests(unittest.TestCase):
    def test_homepage_brand_and_schema(self):
        source = (FRONTEND / "index.html").read_text()
        head = Head(source)
        self.assertEqual(head.meta["og:site_name"], "Hệ thống Base Aitogy")
        self.assertEqual(head.canonicals, ["https://base.aitogy.com/"])
        schemas = [json.loads(value) for value in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', source, re.S
        )]
        website = next(item for item in schemas if item["@type"] == "WebSite")
        self.assertEqual(website["name"], head.meta["og:site_name"])
        self.assertNotIn("potentialAction", website)
        self.assertNotIn("cors", head.meta["description"].lower())

    def test_page_metadata_is_safe_and_independent_of_homepage_copy(self):
        source = (FRONTEND / "index.html").read_text()
        for path in ("station/ha.noi", "blog/ntrip-caster-la-gi", "blog"):
            with self.subTest(path=path):
                url = f"https://base.aitogy.com/{path}"
                title = 'Trạm <Base> & "Aitogy"'
                description = 'Tên trạm "><script>alert(1)</script> & \\1'
                result = page_metadata(source, title=title, description=description, url=url)
                head = Head(result)
                self.assertEqual(head.canonicals, [url])
                for key in ("og:url", "twitter:url"):
                    self.assertEqual(head.meta[key], url)
                for key in ("og:title", "twitter:title"):
                    self.assertEqual(head.meta[key], title)
                for key in ("description", "og:description", "twitter:description"):
                    self.assertEqual(head.meta[key], description)
                self.assertNotIn("<script>alert(1)</script>", result)
                self.assertIn("<title>Trạm &lt;Base&gt; &amp; &quot;Aitogy&quot;</title>", result)


if __name__ == "__main__":
    unittest.main()
