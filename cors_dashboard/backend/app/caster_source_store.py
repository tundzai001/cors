"""Durable mountpoint inventory. Caster outages never erase saved sources."""
import asyncio
import hashlib
import logging
import math
import time

from sqlalchemy import func, select

from . import models
from .caster_client import CasterClient
from .database import auth_session_scope

logger = logging.getLogger('app.main')


def source_domain(host: str) -> str:
    host = host.strip().lower()
    for domain in ('aitogy.com.vn', 'aitogy.click'):
        if domain in host:
            return domain
    return 'localhost' if host == 'localhost' or host.startswith('127.') else host


async def lock_source_host(db, host: str, port: int):
    # Serialize polling with create/delete across backend workers.
    key = int.from_bytes(hashlib.sha256(f'caster-sources:{host.lower()}:{port}'.encode()).digest()[:8], 'big', signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(key)))


def source_values(host: str, port: int, source: dict, observed_at: int) -> dict:
    mountpoint = str(source.get('mountpoint') or '').strip()
    if not mountpoint:
        raise ValueError('Mountpoint is required')
    fields = ('identifier', 'datacount', 'latitude', 'longitude', 'auth_required', 'gga_required',
              'show_position', 'format', 'format_details', 'carrier', 'nav_system', 'network', 'country',
              'counter', 'index', 'overlap_data', 're_positioning', 'storing_data')
    data = {key: source[key] for key in fields if key in source}
    for key in ('latitude', 'longitude'):
        try:
            value = float(data.get(key))
            data[key] = value if math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            data[key] = None
    return {'host':host.strip().lower(), 'port':int(port), 'mountpoint':mountpoint,
                'password':source.get('password') or None, 'source_data':data,
                'created_at':observed_at, 'last_seen_at':observed_at}


async def save_sources(db, host: str, port: int, sources: list[dict], observed_at: int):
    result = await db.execute(select(models.CasterSource).where(
        models.CasterSource.host == host.strip().lower(), models.CasterSource.port == int(port)))
    saved = {row.mountpoint: row for row in result.scalars().all()}
    for source in sources:
        values = source_values(host, port, source, observed_at)
        existing = saved.get(values['mountpoint'])
        if existing:
            existing.source_data = {**existing.source_data, **values['source_data']}
            if values['password'] is not None:
                existing.password = values['password']
            existing.last_seen_at = observed_at
        else:
            row = models.CasterSource(**values)
            db.add(row)
            saved[row.mountpoint] = row


async def sync_caster_sources(host: str, port: int, user=None, password=None) -> dict:
    """Import observations; missing entries and failed polls retain saved rows."""
    try:
        async with auth_session_scope() as db:
            await lock_source_host(db, host, port)
            sources = await CasterClient(host, port, admin_user=user, admin_password=password).get_sources()
            if sources is None:
                logger.warning('[CASTER SOURCES] %s:%s unavailable; keeping saved mountpoints; retry next poll', host, port)
                return {'host': host, 'count': 0, 'synced_at': None,
                        'error': 'Không thể đồng bộ caster; giữ danh sách đã lưu.'}
            observed_at = int(time.time())
            await save_sources(db, host, port, sources, observed_at)
            await db.commit()
        return {'host': host, 'count': len(sources), 'synced_at': observed_at, 'error': None}
    except Exception:
        logger.exception('[CASTER SOURCES] Could not sync %s:%s', host, port)
        return {'host': host, 'count': 0, 'synced_at': None, 'error': 'Không thể đồng bộ caster; giữ danh sách đã lưu.'}


async def sync_configured_sources(hosts, config):
    return await asyncio.gather(*(sync_caster_sources(host, int(config.get('port') or 2101),
                                                     config.get('user'), config.get('pass')) for host in hosts))


def inventory_response(rows, configured_hosts=(), observations=None):
    by_host = {host: [] for host in configured_hosts}
    by_domain = {}
    sources = []
    for row in rows:
        source = {**row.source_data, 'mountpoint': row.mountpoint, 'host': row.host,
                  'port': row.port, 'domain': source_domain(row.host), 'last_seen_at': row.last_seen_at}
        observation = (observations or {}).get(row.host.lower())
        source['on_caster'] = (row.last_seen_at >= observation) if observation is not None else None
        # Never include source passwords in list responses.
        source.pop('password', None)
        sources.append(source)
        by_host.setdefault(row.host, []).append(source)
        by_domain.setdefault(source['domain'], []).append(source)
    return {'total': len(sources), 'all': sources, 'by_host': by_host, 'by_domain': by_domain,
            'last_synced_at': max((row.last_seen_at for row in rows), default=None)}
