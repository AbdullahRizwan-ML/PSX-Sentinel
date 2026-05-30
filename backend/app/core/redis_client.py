"""
PSX Sentinel — Async Redis Client

Wraps redis.asyncio with typed helper methods for:
- Key-value caching with TTL (price data, reports, company info)
- Rate limiting with sliding windows
- Pub/sub event publishing (for real-time frontend updates)
- Circuit breaker state management (for LLM gateway resilience)

All methods fail silently on Redis errors — the application should
degrade gracefully if Redis is temporarily unavailable (cache misses
become database queries, rate limits become permissive).
"""

import json

import redis.asyncio

from app.core.config import get_settings

settings = get_settings()


class RedisClient:
    """
    Async Redis client with typed wrapper functions.

    Connection pool is created once and shared across all operations.
    Max 20 connections prevents resource exhaustion under load while
    supporting concurrent agent operations.
    """

    def __init__(self) -> None:
        self.pool = redis.asyncio.ConnectionPool.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        self.client = redis.asyncio.Redis(connection_pool=self.pool)

    # ── Cache Operations ──────────────────────────────────────────────────────

    async def get_cached(self, key: str) -> str | None:
        """
        Get a cached value by key.

        Returns None if the key doesn't exist or Redis is unreachable.
        Callers should always have a fallback (e.g. database query).
        """
        try:
            return await self.client.get(key)
        except Exception:
            return None

    async def set_cached(
        self, key: str, value: str, ttl_seconds: int
    ) -> None:
        """
        Set a cached value with a TTL (time-to-live) in seconds.

        Fails silently on error — cache writes are best-effort.
        The application continues to function even if caching fails.
        """
        try:
            await self.client.setex(key, ttl_seconds, value)
        except Exception:
            pass

    async def delete_cached(self, key: str) -> None:
        """
        Delete a cached key.

        Fails silently if the key doesn't exist or Redis is unreachable.
        Used for cache invalidation after data updates.
        """
        try:
            await self.client.delete(key)
        except Exception:
            pass

    # ── Rate Limiting ─────────────────────────────────────────────────────────

    async def get_rate_limit_count(self, identifier: str) -> int:
        """
        Get the current rate limit counter for an identifier.

        Returns 0 if not set or on error. Identifiers are typically
        formatted as "user:{user_id}" or "ip:{ip_address}".
        """
        try:
            val = await self.client.get(f"rate:{identifier}")
            return int(val) if val else 0
        except Exception:
            return 0

    async def increment_rate_limit(
        self, identifier: str, window_seconds: int
    ) -> int:
        """
        Increment the rate limit counter with a sliding window.

        Uses a Redis pipeline to atomically increment and set expiry.
        Returns the new count. On error, returns 0 (permissive fallback).
        """
        try:
            pipe = self.client.pipeline()
            await pipe.incr(f"rate:{identifier}")
            await pipe.expire(f"rate:{identifier}", window_seconds)
            results = await pipe.execute()
            return results[0]
        except Exception:
            return 0

    # ── Pub/Sub ───────────────────────────────────────────────────────────────

    async def publish_event(self, channel: str, message: dict) -> None:
        """
        Publish a JSON-serialized event to a Redis pub/sub channel.

        Used for real-time notifications to the frontend (e.g. when
        a new intelligence report is generated, or a price alert fires).
        """
        try:
            await self.client.publish(channel, json.dumps(message))
        except Exception:
            pass

    # ── Circuit Breaker ───────────────────────────────────────────────────────

    async def get_circuit_breaker_failures(self, agent_name: str) -> int:
        """
        Get the current failure count for an agent's circuit breaker.

        Returns 0 if no failures recorded or on error. The LLMGateway
        checks this before every LLM call.
        """
        try:
            val = await self.client.get(f"cb:{agent_name}:failures")
            return int(val) if val else 0
        except Exception:
            return 0

    async def increment_circuit_breaker(self, agent_name: str) -> int:
        """
        Increment the circuit breaker failure count.

        Auto-expires after 300 seconds (5 minutes), giving the external
        service time to recover before the circuit breaker resets.
        Uses a pipeline for atomic increment + expire.
        """
        try:
            pipe = self.client.pipeline()
            await pipe.incr(f"cb:{agent_name}:failures")
            await pipe.expire(f"cb:{agent_name}:failures", 300)
            results = await pipe.execute()
            return results[0]
        except Exception:
            return 0

    async def reset_circuit_breaker(self, agent_name: str) -> None:
        """
        Reset the circuit breaker failure count on a successful call.

        Called by LLMGateway after a successful LLM response, clearing
        any accumulated failure count.
        """
        try:
            await self.client.delete(f"cb:{agent_name}:failures")
        except Exception:
            pass

    # ── Health & Lifecycle ────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """
        Returns True if Redis is reachable via PING.

        Used by the /health endpoint to report infrastructure status.
        """
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """
        Gracefully close the Redis connection.

        Called during application shutdown to release resources.
        """
        await self.client.aclose()


# ── Cache Key Constants ───────────────────────────────────────────────────────
# These templates are used throughout the application for consistent key naming.
# Format with .format(ticker=..., date=...) before passing to get/set methods.

PRICE_CACHE_KEY = "price:{ticker}"  # TTL 300s (5 min)
REPORT_CACHE_KEY = "report:{ticker}:{date}"  # TTL 86400s (24 hr)
COMPANY_CACHE_KEY = "company:{ticker}"  # TTL 3600s (1 hr)

# ── Singleton Instance ────────────────────────────────────────────────────────

redis_client = RedisClient()
