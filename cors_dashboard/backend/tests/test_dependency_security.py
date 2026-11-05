"""Regression checks for the upgraded HTTP and image dependencies."""
import io
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image


class DependencySecurityTests(unittest.TestCase):
    def test_malformed_host_cannot_replace_the_request_path(self):
        app = FastAPI()

        @app.get('/protected')
        def protected(request: Request):
            return {'path': request.url.path}

        with TestClient(app) as client:
            for host in ('example.com/allowed?x=', 'example.com#allowed', 'example.com?allowed='):
                with self.subTest(host=host):
                    response = client.get('/protected', headers={'host': host})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['path'], '/protected')

    def test_pillow_tiff_file_object_roundtrip(self):
        # Keep TIFF BytesIO encoding usable after the security upgrade.
        stream = io.BytesIO()
        with Image.new('RGB', (16, 16), (12, 34, 56)) as image:
            image.save(stream, format='TIFF', compression='packbits')
        stream.seek(0)
        with Image.open(stream) as restored:
            restored.load()
            self.assertEqual(restored.size, (16, 16))
            self.assertEqual(restored.getpixel((8, 8)), (12, 34, 56))
