"""
PSX Sentinel — JWT Authentication & Authorization

Implements a dual-token authentication system:
- Access tokens: short-lived (30 min default), used for API requests
- Refresh tokens: long-lived (7 days default), used to obtain new access tokens

Three FastAPI dependency tiers enforce authorization:
1. get_current_user — any authenticated user
2. get_current_active_user — authenticated + account is active
3. require_pro — authenticated + active + pro subscription tier

All passwords are hashed with bcrypt. Tokens are signed with HS256.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db

settings = get_settings()

# ── Password Hashing ──────────────────────────────────────────────────────────

PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return PASSWORD_CONTEXT.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return PASSWORD_CONTEXT.verify(plain_password, hashed_password)


# ── Token Creation ─────────────────────────────────────────────────────────────


def create_access_token(data: dict) -> str:
    """
    Create a JWT access token with expiry from settings.

    The 'sub' claim should contain the user's UUID as a string.
    The 'type' claim distinguishes access tokens from refresh tokens
    during verification.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )


def create_refresh_token(data: dict) -> str:
    """
    Create a JWT refresh token with 7-day expiry.

    Refresh tokens have type="refresh" and are only accepted by the
    /auth/refresh endpoint — they cannot be used for regular API requests.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )


# ── Token Verification ────────────────────────────────────────────────────────


def verify_token(token: str) -> dict | None:
    """
    Verify a JWT token and return its payload.

    Returns None on any error (expired, invalid signature, malformed).
    The caller is responsible for checking the 'type' claim to ensure
    the correct token type is being used.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None


# ── FastAPI Dependencies ───────────────────────────────────────────────────────

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate JWT from the Authorization header, then return
    the corresponding User object from the database.

    Rejects:
    - Missing or malformed tokens
    - Expired tokens
    - Refresh tokens (only access tokens are accepted)
    - Tokens with invalid or missing 'sub' claim
    - Tokens referencing non-existent users
    """
    token = credentials.credentials
    payload = verify_token(token)

    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def get_current_active_user(
    user: User = Depends(get_current_user),
) -> User:
    """
    Extends get_current_user to also verify the account is active.
    Deactivated accounts receive a 403 Forbidden response.
    """
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Account is deactivated",
        )
    return user


async def require_pro(
    user: User = Depends(get_current_active_user),
) -> User:
    """
    Extends get_current_active_user to enforce pro subscription tier.
    Free-tier users attempting to access pro-only endpoints receive
    a 403 Forbidden response with an upgrade prompt.
    """
    if user.subscription_tier != "pro":
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Pro subscription",
        )
    return user
