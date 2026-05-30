"""
PSX Sentinel — Intelligence, Alerts & Watchlist API Routes

Provides endpoints for browsing intelligence reports, managing user
alerts, and maintaining watchlists. All routes require authentication
via the get_current_active_user dependency.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_user
from app.db.models import (
    Alert,
    Company,
    IntelligenceReport,
    User,
    Watchlist,
)
from app.db.session import get_db
from app.schemas.company import PaginatedResponse
from app.schemas.intelligence import (
    AddWatchlistRequest,
    AlertResponse,
    CreateAlertRequest,
    IntelligenceReportResponse,
    WatchlistItemResponse,
)

router = APIRouter(tags=["Intelligence"])


# ═══════════════════════════════════════════════════════════════════════════════
# Intelligence Reports
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/intelligence/reports",
    response_model=PaginatedResponse,
    summary="List intelligence reports with optional ticker filter",
)
async def list_reports(
    ticker: str | None = Query(
        default=None, description="Filter by ticker"
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    """Fetch paginated intelligence reports, ordered by most recent."""
    try:
        query = select(IntelligenceReport)
        count_query = select(func.count()).select_from(IntelligenceReport)

        if ticker:
            query = query.where(
                IntelligenceReport.ticker == ticker.upper()
            )
            count_query = count_query.where(
                IntelligenceReport.ticker == ticker.upper()
            )

        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * limit
        query = (
            query.order_by(IntelligenceReport.generated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(query)
        reports = result.scalars().all()

        items = [
            IntelligenceReportResponse.model_validate(r).model_dump(
                mode="json"
            )
            for r in reports
        ]
        return PaginatedResponse.create(
            items=items, total=total, page=page, limit=limit
        )

    except Exception as e:
        logger.error(f"Error listing intelligence reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch intelligence reports",
        )


@router.get(
    "/intelligence/reports/{report_id}",
    response_model=IntelligenceReportResponse,
    summary="Get a specific intelligence report by ID",
)
async def get_report_by_id(
    report_id: uuid.UUID,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> IntelligenceReportResponse:
    """Fetch a single intelligence report by its UUID."""
    try:
        result = await db.execute(
            select(IntelligenceReport).where(
                IntelligenceReport.id == report_id
            )
        )
        report = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error fetching report {report_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch intelligence report",
        )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Intelligence report '{report_id}' not found",
        )

    return IntelligenceReportResponse.model_validate(report)


@router.get(
    "/intelligence/watchlist-reports",
    response_model=list[IntelligenceReportResponse],
    summary="Get latest reports for all watchlist tickers",
)
async def get_watchlist_reports(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[IntelligenceReportResponse]:
    """
    For each ticker on the user's watchlist, fetch the most recent
    intelligence report. Returns an empty list for tickers without reports.
    """
    try:
        # Get all watchlist tickers for this user
        watchlist_result = await db.execute(
            select(Watchlist.ticker).where(Watchlist.user_id == user.id)
        )
        watchlist_tickers = [row[0] for row in watchlist_result.all()]

        if not watchlist_tickers:
            return []

        # For each ticker, fetch the latest report using a correlated subquery
        # We use a subquery to get the max generated_at per ticker
        latest_dates = (
            select(
                IntelligenceReport.ticker,
                func.max(IntelligenceReport.generated_at).label(
                    "max_generated"
                ),
            )
            .where(IntelligenceReport.ticker.in_(watchlist_tickers))
            .group_by(IntelligenceReport.ticker)
            .subquery()
        )

        query = select(IntelligenceReport).join(
            latest_dates,
            (IntelligenceReport.ticker == latest_dates.c.ticker)
            & (
                IntelligenceReport.generated_at
                == latest_dates.c.max_generated
            ),
        )

        result = await db.execute(query)
        reports = result.scalars().all()

        return [
            IntelligenceReportResponse.model_validate(r) for r in reports
        ]

    except Exception as e:
        logger.error(f"Error fetching watchlist reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch watchlist reports",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Alerts
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/alerts",
    response_model=list[AlertResponse],
    summary="List all active alerts for the current user",
)
async def list_alerts(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[AlertResponse]:
    """Fetch all active alerts belonging to the authenticated user."""
    try:
        result = await db.execute(
            select(Alert)
            .where(Alert.user_id == user.id, Alert.is_active.is_(True))
            .order_by(Alert.created_at.desc())
        )
        alerts = result.scalars().all()
        return [AlertResponse.model_validate(a) for a in alerts]
    except Exception as e:
        logger.error(f"Error listing alerts for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch alerts",
        )


@router.post(
    "/alerts",
    response_model=AlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new alert",
)
async def create_alert(
    request: CreateAlertRequest,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AlertResponse:
    """
    Create a new alert for the authenticated user.
    Validates that the ticker exists in the Company table.
    """
    # Verify ticker exists
    try:
        company_result = await db.execute(
            select(Company.ticker).where(
                Company.ticker == request.ticker.upper()
            )
        )
        if not company_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{request.ticker}' not found",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error checking ticker: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    try:
        alert = Alert(
            user_id=user.id,
            ticker=request.ticker.upper(),
            alert_type=request.alert_type,
            threshold_value=request.threshold_value,
        )
        db.add(alert)
        await db.flush()

        logger.info(
            f"Alert created: {request.alert_type} for "
            f"{request.ticker.upper()} by user={user.email}"
        )
        return AlertResponse.model_validate(alert)

    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create alert",
        )


@router.delete(
    "/alerts/{alert_id}",
    summary="Deactivate an alert (soft delete)",
)
async def deactivate_alert(
    alert_id: uuid.UUID,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Soft-delete an alert by setting is_active = False.
    Only the alert owner can deactivate their own alerts.
    """
    try:
        result = await db.execute(
            select(Alert).where(Alert.id == alert_id)
        )
        alert = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Database error fetching alert {alert_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found",
        )

    if alert.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only deactivate your own alerts",
        )

    try:
        alert.is_active = False
        await db.flush()
        logger.info(f"Alert {alert_id} deactivated by user={user.email}")
    except Exception as e:
        logger.error(f"Error deactivating alert {alert_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate alert",
        )

    return {"message": "Alert deactivated"}


# ═══════════════════════════════════════════════════════════════════════════════
# Watchlist
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/watchlist",
    response_model=list[WatchlistItemResponse],
    summary="Get the current user's watchlist",
)
async def get_watchlist(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[WatchlistItemResponse]:
    """
    Fetch all watchlist entries for the authenticated user.
    Joins with Company to include the company name.
    """
    try:
        result = await db.execute(
            select(Watchlist, Company.name)
            .join(Company, Watchlist.ticker == Company.ticker)
            .where(Watchlist.user_id == user.id)
            .order_by(Watchlist.added_at.desc())
        )
        rows = result.all()

        return [
            WatchlistItemResponse(
                id=row[0].id,
                ticker=row[0].ticker,
                company_name=row[1],
                added_at=row[0].added_at,
                notes=row[0].notes,
            )
            for row in rows
        ]

    except Exception as e:
        logger.error(f"Error fetching watchlist for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch watchlist",
        )


@router.post(
    "/watchlist",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a ticker to the watchlist",
)
async def add_to_watchlist(
    request: AddWatchlistRequest,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistItemResponse:
    """
    Add a ticker to the user's watchlist.
    - Returns 404 if ticker doesn't exist
    - Returns 409 if already on watchlist
    """
    ticker_upper = request.ticker.upper()

    # Verify ticker exists
    try:
        company_result = await db.execute(
            select(Company).where(Company.ticker == ticker_upper)
        )
        company = company_result.scalar_one_or_none()
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{request.ticker}' not found",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error checking ticker: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    # Check for duplicate
    try:
        existing = await db.execute(
            select(Watchlist).where(
                Watchlist.user_id == user.id,
                Watchlist.ticker == ticker_upper,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"'{ticker_upper}' is already on your watchlist",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error checking watchlist duplicate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    try:
        entry = Watchlist(
            user_id=user.id,
            ticker=ticker_upper,
            notes=request.notes,
        )
        db.add(entry)
        await db.flush()

        logger.info(
            f"Watchlist: {ticker_upper} added by user={user.email}"
        )
        return WatchlistItemResponse(
            id=entry.id,
            ticker=entry.ticker,
            company_name=company.name,
            added_at=entry.added_at,
            notes=entry.notes,
        )

    except Exception as e:
        logger.error(f"Error adding to watchlist: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add to watchlist",
        )


@router.delete(
    "/watchlist/{ticker}",
    summary="Remove a ticker from the watchlist",
)
async def remove_from_watchlist(
    ticker: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a ticker from the user's watchlist (hard delete)."""
    try:
        result = await db.execute(
            select(Watchlist).where(
                Watchlist.user_id == user.id,
                Watchlist.ticker == ticker.upper(),
            )
        )
        entry = result.scalar_one_or_none()
    except Exception as e:
        logger.error(
            f"Database error finding watchlist entry for {ticker}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{ticker.upper()}' is not on your watchlist",
        )

    try:
        await db.delete(entry)
        await db.flush()
        logger.info(
            f"Watchlist: {ticker.upper()} removed by user={user.email}"
        )
    except Exception as e:
        logger.error(f"Error removing from watchlist: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove from watchlist",
        )

    return {"message": "Removed from watchlist"}
