"""First-party page analytics. Browser labels are display hints, never identity proof."""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth, models
from .client_device import client_device_name
from .database import get_auth_db

router = APIRouter()
LOCAL_TZ = timezone(timedelta(hours=7))


class PageSignal(BaseModel):
    view_id: UUID
    visitor_id: UUID
    path: str = Field(min_length=1, max_length=300)
    device_name: str = Field(default='', max_length=100)
    touch: bool = False
    heartbeat: bool = False

    @field_validator('path')
    @classmethod
    def safe_path(cls, value):
        # Queries can carry credentials. Only the known map view is retained.
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not value.startswith('/') or value.startswith('//') or '\\' in value:
            raise ValueError('Expected a local page path')
        return parsed.path + ('?view=map' if parsed.path == '/' and parsed.query == 'view=map' else '')


def device_dimensions(ua, hint, touch=False):
    name = client_device_name(ua, hint)
    os_name, _, browser = name.rpartition(' · ')
    lower = (ua or '').lower()
    device = 'Máy tính'
    if 'ipad' in lower or ('macintosh' in lower and touch):
        device = 'Máy tính bảng'
        os_name = 'iPadOS'
    elif 'iphone' in lower or 'ipod' in lower or ('android' in lower and 'mobile' in lower):
        device = 'Điện thoại'
    elif 'android' in lower:
        device = 'Máy tính bảng'
    if os_name not in {'iPhone · iOS', 'iPod · iOS', 'iPad · iPadOS', 'iPadOS', 'Android', 'Windows 10/11', 'Windows', 'macOS', 'Linux'}:
        os_name = 'Không xác định'
    if browser not in {'Brave', 'Chrome', 'Edge', 'Opera', 'Firefox', 'Safari'}:
        browser = 'Không xác định'
    return device, os_name, browser


@router.post('/api/public/analytics/page', status_code=204, responses={403: {"description": "Forbidden"}})
async def record_page(signal: PageSignal, request: Request, db: AsyncSession = Depends(get_auth_db)):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, 'Cross-origin analytics rejected')
    ua = request.headers.get('user-agent', '')
    if any(word in ua.lower() for word in ('bot', 'spider', 'crawler', 'headless')):
        return Response(status_code=204)
    now = int(time.time())
    device, os_name, browser = device_dimensions(ua, signal.device_name, signal.touch)
    view = models.AnalyticsPageView
    if signal.heartbeat:
        # Heartbeats never create page views, including after a failed initial signal.
        from sqlalchemy import update
        await db.execute(update(view).where(view.view_id == str(signal.view_id),
            view.visitor_id == str(signal.visitor_id)).values(last_seen_at=now))
    else:
        statement = insert(view).values(view_id=str(signal.view_id), visitor_id=str(signal.visitor_id),
            path=signal.path, device=device, os=os_name, browser=browser, created_at=now, last_seen_at=now)
        await db.execute(statement.on_conflict_do_nothing(index_elements=['view_id']))
    await db.commit()
    return Response(status_code=204)


@router.get('/api/admin/analytics', responses={403: {"description": "Forbidden"}, 422: {"description": "Unprocessable Entity"}})
async def analytics_report(response: Response, days: int = Query(28, ge=1, le=90),
                           db: AsyncSession = Depends(get_auth_db),
                           user: models.User = Depends(auth.get_current_user)):
    if user.role != auth.Role.ADMIN:
        raise HTTPException(403, 'Chỉ quản trị viên được xem Analytics')
    if not 1 <= days <= 90:
        raise HTTPException(422, 'days phải từ 1 đến 90')
    response.headers['Cache-Control'] = 'no-store'
    now = int(time.time())
    today = datetime.fromtimestamp(now, LOCAL_TZ).date()
    start_date = today - timedelta(days=days - 1)
    since = int(datetime.combine(start_date, datetime.min.time(), LOCAL_TZ).timestamp())
    view = models.AnalyticsPageView
    count = func.count()
    visitors = func.count(distinct(view.visitor_id))
    total = (await db.execute(select(count, visitors).where(view.created_at >= since))).one()
    traffic_started_at = (await db.execute(select(func.min(view.created_at)))).scalar_one()
    session = models.UserSession
    login_total, login_accounts = (await db.execute(select(
        func.count(), func.count(distinct(session.user_id))
    ).where(session.created_at >= since))).one()
    login_started_at = (await db.execute(select(func.min(session.created_at)))).scalar_one()
    login_day = func.floor((session.created_at + 25200) / 86400)
    login_rows = (await db.execute(select(login_day, func.count())
        .where(session.created_at >= since).group_by(login_day))).all()
    login_map = {int(row[0]): row[1] for row in login_rows}
    first = select(func.min(view.created_at).label('first_seen')).group_by(view.visitor_id).subquery()
    new_visitors = (await db.execute(select(func.count()).select_from(first).where(first.c.first_seen >= since))).scalar_one()
    active = (await db.execute(select(visitors).where(view.last_seen_at >= now - 1800))).scalar_one()
    day = func.floor((view.created_at + 25200) / 86400)
    daily_rows = (await db.execute(select(day, count, visitors).where(view.created_at >= since).group_by(day))).all()
    daily_map = {int(row[0]): (row[1], row[2]) for row in daily_rows}
    daily = []
    for i in range(days):
        date = start_date + timedelta(days=i)
        key = (date - datetime(1970, 1, 1).date()).days
        views, unique = daily_map.get(key, (0, 0))
        traffic_known = traffic_started_at is not None and key >= (traffic_started_at + 25200) // 86400
        logins_known = login_started_at is not None and key >= (login_started_at + 25200) // 86400
        daily.append({'date': date.isoformat(), 'views': views if traffic_known else None,
            'visitors': unique if traffic_known else None,
            'logins': login_map.get(key, 0) if logins_known else None})
    breakdown = {}
    for dimension in ('path', 'device', 'os', 'browser'):
        column = getattr(view, dimension)
        rows = (await db.execute(select(column, count, visitors).where(view.created_at >= since)
            .group_by(column).order_by(count.desc(), column).limit(20))).all()
        breakdown[dimension] = [{'name': row[0], 'views': row[1], 'visitors': row[2]} for row in rows]
    minute = func.floor(view.last_seen_at / 60)
    rows = (await db.execute(select(minute, visitors).where(view.last_seen_at >= now - 1800).group_by(minute))).all()
    minute_map = {int(row[0]): row[1] for row in rows}
    return {'days': days, 'timezone': 'Asia/Ho_Chi_Minh', 'updated_at': now,
        'totals': {'views': total[0], 'visitors': total[1], 'new_visitors': new_visitors, 'active': active},
        'traffic_started_at': traffic_started_at,
        'logins': {'total': login_total, 'accounts': login_accounts, 'started_at': login_started_at},
        'daily': daily, 'breakdown': breakdown,
        'realtime': [{'minute': key * 60, 'visitors': minute_map.get(key, 0)} for key in range(now // 60 - 29, now // 60 + 1)]}
