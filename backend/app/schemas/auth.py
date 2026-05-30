"""
PSX Sentinel — Authentication Schemas

Pydantic v2 request/response models for the auth endpoints.
All models use ConfigDict(from_attributes=True) where they map
directly to SQLAlchemy ORM objects, enabling automatic serialization.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    """Registration payload. Password must be 8-100 characters."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=100)
    full_name: str = Field(min_length=2, max_length=255)


class UserLoginRequest(BaseModel):
    """Login payload. Email + plaintext password."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token pair returned on successful authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expiry


class UserResponse(BaseModel):
    """Public user profile. Never exposes hashed_password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    subscription_tier: str
    is_active: bool
    created_at: datetime


class RefreshRequest(BaseModel):
    """Request body for token refresh and logout endpoints."""

    refresh_token: str
