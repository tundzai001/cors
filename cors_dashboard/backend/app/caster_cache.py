"""Shared caster cache and its synchronization lock."""

import asyncio
import logging
from typing import Optional

from .redis_client import redis_client

logger = logging.getLogger("app.main")


global_caster_data_cache = {}
global_caster_cache_lock = asyncio.Lock()


async def _set_caster_cache(key: str, data: dict) -> None:
    global_caster_data_cache[key] = data
    try:
        await redis_client.set_caster_cache(key, data)
    except Exception as exc:
        logger.debug("Redis caster cache write skipped for %s: %s", key, exc)


async def _get_caster_cache(key: str) -> Optional[dict]:
    cached = global_caster_data_cache.get(key)
    if cached is not None:
        return cached
    try:
        return await redis_client.get_caster_cache(key)
    except Exception as exc:
        logger.debug("Redis caster cache read skipped for %s: %s", key, exc)
        return None


async def _get_all_caster_cache() -> dict:
    if global_caster_data_cache:
        return dict(global_caster_data_cache)
    try:
        return await redis_client.get_all_caster_cache()
    except Exception as exc:
        logger.debug("Redis caster cache read-all skipped: %s", exc)
        return {}

