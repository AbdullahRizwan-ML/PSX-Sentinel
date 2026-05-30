"""
PSX Sentinel — FastAPI Application Entry Point

This is the main application factory. It configures:
1. Loguru logging with console + rotating file output
2. FastAPI lifespan events (DB init on startup, Redis close on shutdown)
3. CORS middleware for frontend communication
4. Request timing middleware (X-Response-Time header)
5. IP-based rate limiting middleware (100 req/min)
6. Global exception handler for uncaught errors
7. Router registration for all API v1 endpoints

Start the server with:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import get_settings
from app.core.redis_client import redis_client
from app.db.session import init_db

settings = get_settings()

# ── Loguru Configuration ──────────────────────────────────────────────────────
# Remove default stderr handler and replace with custom formatters.

logger.remove()

# Console output — colorized, includes module and line number
logger.add(
    sys.stdout,
    format=(
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
        "{name}:{line} | {message}"
    ),
    level=settings.LOG_LEVEL,
    colorize=True,
)

# Rotating file output — daily rotation, 30-day retention, compressed
logger.add(
    "logs/psx_sentinel_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    compression="gz",
    level="INFO",
)


# ── Application Lifespan ──────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown events.

    Startup:
    - Initialize database tables (create_all)
    - Log configuration summary

    Shutdown:
    - Close Redis connection pool
    """
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("PSX Sentinel starting up...")
    await init_db()
    logger.info("Database initialized")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Monitoring tickers: {settings.PSX_TICKERS}")
    logger.info(
        f"Nightly pipeline scheduled at {settings.NIGHTLY_PIPELINE_HOUR}:00 PKT"
    )
    logger.info("PSX Sentinel ready [OK]")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("PSX Sentinel shutting down...")
    await redis_client.close()
    logger.info("Redis connection closed")
    logger.info("Shutdown complete")


# ── FastAPI Application ───────────────────────────────────────────────────────

app = FastAPI(
    title="PSX Sentinel",
    description=(
        "Enterprise-grade AI financial intelligence "
        "for the Pakistan Stock Exchange"
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# ── CORS Middleware ───────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Timing Middleware ─────────────────────────────────────────────────


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """
    Measure request processing time and add X-Response-Time header.
    Also logs method, path, status code, and duration for observability.
    """
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    logger.debug(
        f"{request.method} {request.url.path} "
        f"→ {response.status_code} [{duration_ms}ms]"
    )
    return response


# ── Rate Limiting Middleware ──────────────────────────────────────────────────


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    IP-based rate limiting: 100 requests per minute per IP.

    Skips rate limiting for documentation and health endpoints.
    Returns 429 Too Many Requests when limit is exceeded.
    On Redis failure, permits the request (permissive fallback).
    """
    skip_paths = {
        "/api/docs",
        "/api/redoc",
        "/api/v1/health",
        "/api/openapi.json",
    }
    if request.url.path in skip_paths:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    count = await redis_client.increment_rate_limit(
        identifier=client_ip,
        window_seconds=60,
    )

    if count > 100:
        logger.warning(
            f"Rate limit exceeded for IP {client_ip}: "
            f"{count} requests in 60s"
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Rate limit exceeded. Max 100 requests/minute."
            },
        )

    return await call_next(request)


# ── Global Exception Handler ─────────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all handler for unhandled exceptions.

    Logs the full error with request context and returns a generic
    500 response to the client. Never exposes internal error details.
    """
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "An internal error occurred. Our team has been notified."
            ),
            "type": type(exc).__name__,
        },
    )


# ── Router Registration ──────────────────────────────────────────────────────

from app.api.v1 import auth, companies, health, intelligence  # noqa: E402

app.include_router(auth.router, prefix="/api/v1/auth")
app.include_router(companies.router, prefix="/api/v1")
app.include_router(intelligence.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
