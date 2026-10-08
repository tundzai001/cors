import json
import logging
import os
import asyncio
import time
from typing import Any, List, Optional

import redis.asyncio as redis

from .database import settings

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self.pool = None
        self._redis = None
        self.enabled = str(os.getenv("REDIS_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
        self._fallback_bans: set[str] = set()
        self._fallback_limits: dict[str, tuple[int, float]] = {}
        self._fallback_lock = asyncio.Lock()

    async def connect(self):
        if not self.enabled:
            logger.info("Redis disabled; using in-memory fallbacks")
            return

        try:
            redis_url = os.getenv("REDIS_URL") or getattr(settings, "REDIS_URL", None) or "redis://localhost:6379/0"
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
            return ip in self._fallback_bans
        return bool(await self._redis.sismember("banned_ips", ip))

    async def ban_ip(self, ip: str):
        if not self._redis:
            self._fallback_bans.add(ip)
            return
        await self._redis.sadd("banned_ips", ip)

    async def unban_ip(self, ip: str):
        if not self._redis:
            self._fallback_bans.discard(ip)
            return
        await self._redis.srem("banned_ips", ip)

    async def get_banned_ips(self) -> List[str]:
        if not self._redis:
            return sorted(self._fallback_bans)
        return list(await self._redis.smembers("banned_ips"))

    async def check_rate_limit(self, key: str, limit: int, window: int = 60) -> bool:
        if not self._redis:
            now = time.monotonic()
            async with self._fallback_lock:
                count, expires_at = self._fallback_limits.get(key, (0, 0.0))
                if expires_at <= now:
                    count, expires_at = 0, now + window
                if count >= limit:
                    self._fallback_limits[key] = (count, expires_at)
                    return False
                self._fallback_limits[key] = (count + 1, expires_at)
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
