"""Analytics collection and reports exercised against a real SQL store."""
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from datetime import datetime
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app import auth, models
from app.analytics_routes import router
from app.database import get_auth_db


class AnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection:
            for table in (models.User.__table__, models.UserSession.__table__, models.AnalyticsPageView.__table__):
                await connection.run_sync(table.create)
        sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.sessions = sessions
        async def database():
            async with sessions() as session:
                yield session
        self.user = SimpleNamespace(id=1, role='admin')
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_auth_db] = database
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.user
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url='https://example.test')
        self.signal = {'view_id':str(uuid4()), 'visitor_id':str(uuid4()), 'path':'/security?token=secret#test', 'device_name':'macOS · Brave'}
        self.ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def send(self, **changes):
        return await self.client.post('/api/public/analytics/page', json={**self.signal, **changes}, headers={'user-agent':self.ua,'origin':'https://example.test'})

    async def report(self, days=28):
        response = await self.client.get(f'/api/admin/analytics?days={days}')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        return response.json()

    async def test_deduplication_heartbeat_and_dimensions(self):
        await self.send(heartbeat=True)
        self.assertEqual((await self.report())['totals']['views'],0)
        for _ in range(2):
            self.assertEqual((await self.send()).status_code,204)
        await self.send(heartbeat=True)
        await self.send(view_id=str(uuid4()),path='/?view=map')
        report = await self.report()
        self.assertEqual(report['totals'],{'views':2,'visitors':1,'new_visitors':1,'active':1})
        self.assertEqual(sum(row['views'] or 0 for row in report['daily']),2)
        self.assertEqual(report['breakdown']['browser'][0]['name'],'Brave')
        self.assertEqual({row['name'] for row in report['breakdown']['path']},{'/security','/?view=map'})
        self.assertEqual(len(report['daily']),28)
        self.assertEqual(len(report['realtime']),30)

    async def test_local_midnight_returning_visitor_and_expiry(self):
        now = int(datetime.fromisoformat('2026-10-28T00:00:10+07:00').timestamp())
        with patch('app.analytics_routes.time.time',return_value=now-20):
            await self.send()
        with patch('app.analytics_routes.time.time',return_value=now):
            await self.send(view_id=str(uuid4()))
            report = await self.report(days=1)
            self.assertEqual(report['totals']['views'],1)
            self.assertEqual(report['totals']['new_visitors'],0)
            self.assertEqual(report['daily'][0]['date'],'2026-10-28')
        with patch('app.analytics_routes.time.time',return_value=now+1801):
            self.assertEqual((await self.report())['totals']['active'],0)

    async def test_access_validation_and_bot_filter(self):
        for path in ('https://other.test/', '//other.test/', '/\\evil'):
            self.assertEqual((await self.send(path=path)).status_code,422)
        self.assertEqual((await self.send(visitor_id='invalid')).status_code,422)
        blocked = await self.client.post('/api/public/analytics/page',json=self.signal,headers={'origin':'https://other.test'})
        self.assertEqual(blocked.status_code,403)
        self.ua='Googlebot'
        await self.send()
        self.assertEqual((await self.report())['totals']['views'],0)
        self.user.role='viewer'
        self.assertEqual((await self.client.get('/api/admin/analytics')).status_code,403)
        self.app.dependency_overrides.pop(auth.get_current_user)
        self.assertIn((await self.client.get('/api/admin/analytics')).status_code,(401,403))


    async def test_historical_logins_are_separate_from_browser_views(self):
        now = int(datetime.fromisoformat('2026-10-28T23:00:00+07:00').timestamp())
        async with self.sessions() as db:
            for uid in (1,2):
                db.add(models.User(id=uid,username=f'user{uid}',hashed_password='unused',created_at=now-86400))
            for i,uid,seconds_ago in ((1,1,86400),(2,1,3600),(3,2,60)):
                db.add(models.UserSession(session_id=f'login{i}',user_id=uid,username=f'user{uid}',role='viewer',
                    ip_address='192.0.2.1',device_id='same-browser',device_name='macOS · Brave',
                    created_at=now-seconds_ago,last_active_at=now,expires_at=now-1 if i==1 else now+3600,
                    is_revoked=i==2))
            await db.commit()
        with patch('app.analytics_routes.time.time',return_value=now):
            await self.send()
            await self.send(heartbeat=True)
            report = await self.report()
            self.assertEqual(report['logins']['total'],3)
            self.assertEqual(report['logins']['accounts'],2)
            self.assertEqual(report['totals']['views'],1)
            self.assertEqual(sum(row['logins'] or 0 for row in report['daily']),3)
            self.assertIsNone(report['daily'][-2]['views'],'Unmeasured days must not be invented as zero visits')
            self.assertEqual(report['daily'][-2]['logins'],1)
            self.assertEqual(report['daily'][-1]['logins'],2)
            self.assertEqual(report['daily'][-1]['views'],1)
            self.assertIsNotNone(report['traffic_started_at'])
            today = await self.report(days=1)
            self.assertEqual(today['logins']['total'],2)

    async def test_no_collected_history_is_unknown_in_daily_series(self):
        report = await self.report()
        self.assertEqual(report['logins']['total'],0)
        self.assertIsNone(report['logins']['started_at'])
        self.assertIsNone(report['traffic_started_at'])
        self.assertTrue(all(row['views'] is None and row['logins'] is None for row in report['daily']))
