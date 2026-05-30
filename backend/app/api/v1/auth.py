"""
PSX Sentinel — Authentication API Routes

Handles user registration, login, token refresh, logout, and profile.
All tokens contain {"sub": str(user.id)} as the subject claim.

Logout uses a Redis blacklist to invalidate refresh tokens — the
blacklist entry TTL matches the refresh token's remaining lifetime.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import redis_client
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_current_active_user,
    hash_password,
    verify_password,
    verify_token,
)
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import (
    RefreshRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

settings = get_settings()

router = APIRouter(tags=["Authentication"])


def _build_token_response(user_id: str) -> TokenResponse:
    """Build a TokenResponse with both access and refresh tokens."""
    token_data = {"sub": user_id}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Create a new user account and return JWT tokens.

    - Checks for duplicate email (409 Conflict)
    - Hashes password with bcrypt
    - Creates User record
    - Returns access + refresh token pair
    """
    try:
        result = await db.execute(
            select(User).where(User.email == request.email)
        )
        existing_user = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Database error during registration check: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during registration",
        )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    hashed = hash_password(request.password)
    user = User(
        email=request.email,
        hashed_password=hashed,
        full_name=request.full_name,
    )

    try:
        db.add(user)
        await db.flush()
    except Exception as e:
        logger.error(f"Database error creating user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        )

    logger.info(f"New user registered: {user.email} (id={user.id})")
    return _build_token_response(str(user.id))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate user and return JWT tokens.

    - Returns 401 on invalid email or password
    - Returns 403 if account is deactivated
    """
    try:
        result = await db.execute(
            select(User).where(User.email == request.email)
        )
        user = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Database error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during authentication",
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    logger.info(f"User logged in: {user.email}")
    return _build_token_response(str(user.id))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using refresh token",
)
async def refresh(request: RefreshRequest) -> TokenResponse:
    """
    Exchange a valid refresh token for a new token pair.

    - Validates the refresh token signature and expiry
    - Checks that it's a refresh token (type="refresh")
    - Checks that the token is not blacklisted (logged out)
    - Returns a new access token with the same refresh token
    """
    payload = verify_token(request.refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Check if token has been blacklisted (via logout)
    blacklisted = await redis_client.get_cached(
        f"blacklist:{request.refresh_token}"
    )
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Return new access token but keep the same refresh token
    token_data = {"sub": user_id}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=request.refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    summary="Logout and invalidate refresh token",
)
async def logout(request: RefreshRequest) -> dict:
    """
    Blacklist the refresh token in Redis so it cannot be reused.

    The blacklist entry TTL is set to 7 days (matching the refresh
    token lifetime) — after that the token would have expired anyway.
    """
    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    await redis_client.set_cached(
        key=f"blacklist:{request.refresh_token}",
        value="1",
        ttl_seconds=ttl_seconds,
    )

    logger.info("User logged out — refresh token blacklisted")
    return {"message": "Successfully logged out"}


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    user: User = Depends(get_current_active_user),
) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse.model_validate(user)
