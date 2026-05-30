"""
PSX Sentinel — Async Database Session Management

Provides both async (for application use) and sync (for Alembic migrations)
database engines. The get_db() dependency yields a session that auto-commits
on success and rolls back on any exception, ensuring data integrity without
requiring manual transaction management in every route handler.

Pool configuration:
- pool_pre_ping=True: validates connections before use (handles PostgreSQL restarts)
- pool_recycle=300: recycles connections every 5 minutes (prevents stale connections)
- pool_size=10, max_overflow=20: supports up to 30 concurrent connections

SSL handling:
- Neon Cloud and other managed PostgreSQL services use ?sslmode=require
- asyncpg requires ssl="require" as a connect_arg, not a URL query parameter
- psycopg2 accepts sslmode as a URL parameter natively
- This module strips sslmode from the asyncpg URL and passes it via connect_args
"""

import ssl
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import Base

settings = get_settings()


def _build_async_url_and_args(database_url: str) -> tuple[str, dict]:
    """
    Parse the DATABASE_URL and separate SSL params for asyncpg.

    asyncpg does not accept sslmode/channel_binding as URL query params.
    We strip them and pass ssl=True via connect_args instead.
    Returns (cleaned_url, connect_args_dict).
    """
    parsed = urlparse(database_url)
    query_params = parse_qs(parsed.query)

    # Check if SSL is required
    needs_ssl = "sslmode" in query_params and query_params["sslmode"][0] in (
        "require",
        "verify-ca",
        "verify-full",
    )

    # Remove asyncpg-incompatible params from query string
    incompatible_params = {"sslmode", "channel_binding"}
    filtered_params = {
        k: v[0] for k, v in query_params.items() if k not in incompatible_params
    }
    new_query = urlencode(filtered_params) if filtered_params else ""
    cleaned_url = urlunparse(parsed._replace(query=new_query))

    connect_args = {}
    if needs_ssl:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context

    return cleaned_url, connect_args


# ── Async Engine (Application) ────────────────────────────────────────────────

_async_url, _async_connect_args = _build_async_url_and_args(settings.DATABASE_URL)

async_engine = create_async_engine(
    _async_url,
    echo=settings.ENVIRONMENT == "development",
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=10,
    max_overflow=20,
    connect_args=_async_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ── Sync Engine (Alembic Migrations Only) ─────────────────────────────────────
# psycopg2 handles sslmode natively in the URL, so no special handling needed.

_sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")

sync_engine = create_engine(
    _sync_url,
    pool_pre_ping=True,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session.

    Usage in route handlers:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...

    The session auto-commits on success and rolls back on any exception.
    The finally block ensures the session is always closed, even if the
    commit or rollback itself fails.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Create all tables defined in the ORM models.

    Called on application startup via the FastAPI lifespan event.
    In production, Alembic migrations are preferred — this function
    serves as a convenience for development and testing.
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
