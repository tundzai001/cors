import json
import logging
import os
from typing import Any, List, Optional

import redis.asyncio as redis

from .database import settings

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self.pool = None
        self._redis = None
        self.enabled = str(os.getenv("REDIS_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}

    async def connect(self):
        if not self.enabled:
            logger.info("Redis disabled; using in-memory fallbacks")
            return

        try:
            redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
            self.pool = redis.ConnectionPool.from_url(redis_url, decode_responses=True)
            self._redis = redis.Redis(connection_pool=self.pool)
            await self._redis.ping()
            logger.info("Connected to Redis successfully")
        except Exception as exc:
            logger.warning("Redis unavailable; continuing with in-memory fallbacks: %s", exc)
            self._redis = None

    async def disconnect(self):
        if self._redis:
            await self._redis.aclose()
            logger.info("Disconnected from Redis")
        self._redis = None
        self.pool = None

    @property
    def client(self) -> Optional[redis.Redis]:
        return self._redis

    async def get_json(self, key: str) -> Optional[Any]:
        if not self._redis:
            return None
        val = await self._redis.get(key)
        return json.loads(val) if val else None

    async def set_json(self, key: str, value: Any, expire: int = None):
        if not self._redis:
            return
        await self._redis.set(key, json.dumps(value), ex=expire)

    async def is_ip_banned(self, ip: str) -> bool:
        if not self._redis:
            return False
        return bool(await self._redis.sismember("banned_ips", ip))

    async def ban_ip(self, ip: str):
        if not self._redis:
            return
        await self._redis.sadd("banned_ips", ip)

    async def unban_ip(self, ip: str):
        if not self._redis:
            return
        await self._redis.srem("banned_ips", ip)

    async def get_banned_ips(self) -> List[str]:
        if not self._redis:
            return []
        return list(await self._redis.smembers("banned_ips"))

    async def check_rate_limit(self, key: str, limit: int, window: int = 60) -> bool:
        if not self._redis:
            return True

        current = await self._redis.get(key)
        if current and int(current) >= limit:
            return False

        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        await pipe.execute()
        return True

    async def set_caster_cache(self, key: str, data: dict):
        if not self._redis:
            return
        await self._redis.hset("caster_cache", key, json.dumps(data))

    async def get_caster_cache(self, key: str) -> Optional[dict]:
        if not self._redis:
            return None
        val = await self._redis.hget("caster_cache", key)
        return json.loads(val) if val else None

    async def get_all_caster_cache(self) -> dict:
        if not self._redis:
            return {}
        all_data = await self._redis.hgetall("caster_cache")
        return {k: json.loads(v) for k, v in all_data.items()}


redis_client = RedisClient()
