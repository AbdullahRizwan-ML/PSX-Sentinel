"""
PSX Sentinel — Company & Market Data Schemas

Pydantic v2 models for company listings, price data, announcements,
news articles, and market summary endpoints.
"""

import math
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CompanyResponse(BaseModel):
    """Summary view of a PSX-listed company."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    name: str
    sector: str
    market_cap_pkr: float | None
    is_kse30: bool
    is_kmi30: bool
    last_updated: datetime


class CompanyDetailResponse(CompanyResponse):
    """
    Detailed company view with latest price and conviction score.
    The extra fields are populated from joins — they are not direct
    model attributes.
    """

    shares_outstanding: int | None
    listing_date: date | None
    latest_price: float | None = None
    latest_change_pct: float | None = None
    latest_conviction_score: float | None = None


class PricePoint(BaseModel):
    """Single day OHLCV price data."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    change_pct: float | None


class AnnouncementResponse(BaseModel):
    """PSX corporate announcement (filing, dividend, board meeting)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    announced_at: datetime
    title: str
    category: str
    pdf_url: str | None
    pdf_parsed: bool
    fiscal_quarter: int | None
    fiscal_year: int | None


class NewsArticleResponse(BaseModel):
    """Financial news article from Pakistani media."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    source: str
    headline: str
    summary: str | None
    url: str
    published_at: datetime
    sentiment_score: float | None


class MarketSummaryResponse(BaseModel):
    """Daily market overview — top gainers, losers, and statistics."""

    top_gainers: list[dict[str, Any]]  # [{ticker, name, change_pct, close}]
    top_losers: list[dict[str, Any]]
    total_companies: int
    market_date: date


class PaginatedResponse(BaseModel):
    """
    Generic paginated wrapper.

    Typed subclasses should override 'items' with the correct type,
    but this base provides the pagination metadata.
    """

    total: int
    page: int
    limit: int
    pages: int
    items: list[Any]

    @classmethod
    def create(
        cls, items: list[Any], total: int, page: int, limit: int
    ) -> "PaginatedResponse":
        """Factory method that calculates pages automatically."""
        return cls(
            total=total,
            page=page,
            limit=limit,
            pages=max(1, math.ceil(total / limit)) if limit > 0 else 1,
            items=items,
        )
