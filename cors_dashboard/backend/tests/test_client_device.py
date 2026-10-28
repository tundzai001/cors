import json
from pathlib import Path
import unittest
from app.client_device import client_device_name


class ClientDeviceTests(unittest.TestCase):
    def test_current_and_historical_mobile_labels(self):
        cases = json.loads((Path(__file__).resolve().parents[3] / 'scripts/fixtures/session-devices.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(expected=case['expected']):
                self.assertEqual(client_device_name(case['ua'], case.get('fallback', '')), case['expected'])
        self.assertEqual(client_device_name('', 'My device'), 'My device')
        self.assertEqual(client_device_name(None), 'Thiết bị')


class SessionBrowserHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_refreshes_only_the_authenticated_session_label(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from urllib.parse import quote
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from starlette.requests import Request
        from app import auth, models
        from app.auth_routes import session_heartbeat

        engine = create_engine('sqlite:///:memory:')
        models.User.__table__.create(engine)
        models.UserSession.__table__.create(engine)
        ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130.0.0.0 Safari/537.36'
        token = auth.create_access_token({'sub':'viewer','sid':'one'})
        request = Request({'type':'http','headers':[
            (b'authorization',f'Bearer {token}'.encode()),
            (b'user-agent',ua.encode()),(b'x-device-name',quote('macOS · Brave').encode())]})
        try:
            with Session(engine,expire_on_commit=False) as store:
                for sid in ('one','two'):
                    store.add(models.UserSession(session_id=sid,user_id=1,username='viewer',role='viewer',
                        ip_address='192.0.2.1',device_id=sid,device_name='macOS · Chrome',user_agent=ua,
                        created_at=1,last_active_at=1,expires_at=9999999999,is_revoked=False))
                store.commit()
                db = SimpleNamespace(execute=AsyncMock(side_effect=store.execute),commit=AsyncMock(side_effect=store.commit))
                await session_heartbeat(request=request,current_user=SimpleNamespace(id=1),db=db)
                labels = {row.session_id:row.device_name for row in store.query(models.UserSession).all()}
                self.assertEqual(labels,{'one':'macOS · Brave','two':'macOS · Chrome'})
        finally:
            engine.dispose()
