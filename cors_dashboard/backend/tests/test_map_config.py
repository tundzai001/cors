"""Only browser-safe Mapbox configuration may cross the public API boundary."""
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.frontend_routes import router


class MapConfigTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_public_token_is_available_without_exposing_other_environment_values(self):
        with patch('app.frontend_routes._get_env_preferring_dotenv', return_value='pk.public-example') as read:
            response = self.client.get('/api/public/map-config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'mapbox_public_token': 'pk.public-example'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        read.assert_called_once_with('MAPBOX_PUBLIC_TOKEN', '')

    def test_secret_or_missing_tokens_are_never_published(self):
        for value in ('sk.secret-example', '', 'not-a-token'):
            with self.subTest(value=value), patch('app.frontend_routes._get_env_preferring_dotenv', return_value=value):
                self.assertEqual(self.client.get('/api/public/map-config').json(), {'mapbox_public_token': ''})
