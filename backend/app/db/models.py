"""
PSX Sentinel — SQLAlchemy 2.0 Declarative Models

All models use the modern Mapped[] / mapped_column() pattern.
UUID primary keys use PostgreSQL-native UUID type for performance.
Timestamps use server_default=func.now() so the database generates them,
ensuring consistency even when clocks differ between app servers.

Relationship loading is lazy by default — agents and API routes should
use selectinload() or joinedload() explicitly to avoid N+1 queries.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Base class for all PSX Sentinel ORM models."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. User
# ═══════════════════════════════════════════════════════════════════════════════

class User(Base):
    """
    Platform user. Supports free and pro subscription tiers.
    Pro users get access to full intelligence reports and priority
    analysis queue.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    full_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    subscription_tier: Mapped[str] = mapped_column(
        String(20), default="free"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    watchlists: Mapped[list["Watchlist"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email} [{self.subscription_tier}]>"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Company
# ═══════════════════════════════════════════════════════════════════════════════

class Company(Base):
    """
    PSX-listed company. Ticker is the natural primary key (e.g. "ENGRO").
    KSE-30 and KMI-30 flags indicate index membership for filtering.
    """
    __tablename__ = "companies"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    market_cap_pkr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    shares_outstanding: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    listing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delisted_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )  # PSX formal delisting effective date; NULL = still listed
    is_kse30: Mapped[bool] = mapped_column(Boolean, default=False)
    is_kmi30: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    prices: Mapped[list["DailyPrice"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    fundamentals: Mapped[Optional["CompanyFundamentals"]] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    news: Mapped[list["NewsArticle"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    reports: Mapped[list["IntelligenceReport"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    watchlists: Mapped[list["Watchlist"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Company {self.ticker}: {self.name}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DailyPrice
# ═══════════════════════════════════════════════════════════════════════════════

class DailyPrice(Base):
    """
    Daily OHLCV price data for a company. Scraped from PSX / data providers.
    The unique constraint on (ticker, date) prevents duplicate entries from
    re-scraping the same trading day.
    """
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_price_ticker_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trading_day: Mapped[bool] = mapped_column(Boolean, default=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return f"<DailyPrice {self.ticker} {self.date} close={self.close}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 3b. CompanyFundamentals
# ═══════════════════════════════════════════════════════════════════════════════

class CompanyFundamentals(Base):
    """
    Point-in-time fundamentals snapshot for a company, one row per ticker
    (upserted on each collector run). Sourced from PSX Terminal
    (psxterminal.com) — a free single-maintainer mirror, so every metric
    is nullable: a ticker the source doesn't cover (e.g. ENGRO, which
    PSX Terminal only lists as post-merger ENGROH) simply has no row,
    and a covered ticker may still be missing individual fields.

    dividend_yield and free_float_pct are percentages (3.79 = 3.79%);
    market_cap_pkr is full rupees.
    """
    __tablename__ = "company_fundamentals"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_fundamentals_ticker"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    pe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    market_cap_pkr: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    free_float_pct: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="psx_terminal"
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="fundamentals")

    def __repr__(self) -> str:
        return (
            f"<CompanyFundamentals {self.ticker} pe={self.pe_ratio} "
            f"yield={self.dividend_yield}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3c. InstitutionalFlow (FIPI/LIPI)
# ═══════════════════════════════════════════════════════════════════════════════

class InstitutionalFlow(Base):
    """
    One day's FIPI/LIPI portfolio-investment row from NCCPL
    (nccpl.com.pk): buy/sell/net turnover for one investor category, in
    one market type, optionally within one sector. NOT per-ticker — the
    finest granularity NCCPL publishes is sector level (verified live,
    2026-07-17).

    dataset values: fipi_normal, lipi_normal (market-wide; sector_code
    NULL) and fipi_sector_wise, lipi_sector_wise (sector rows).
    client_type examples: FOREIGN CORPORATES, OVERSEAS PAKISTANI,
    FOREIGN INDIVIDUAL (FIPI); INDIVIDUALS, COMPANIES, BANKS/DFI,
    MUTUAL FUNDS, ... (LIPI). market_type: REG, FUT, BNB, GEM, NDM, ODL
    (older archive rows spell it REGULAR/FUTURE...). Values are PKR;
    sell/net columns can be negative (sells are served negative at the
    source and stored as-is). usd_value is NCCPL's own USD conversion
    of net_value. Source TOTAL rollup rows are derived data and are NOT
    stored.

    NOTE (2026-07-17): no collector exists yet — NCCPL sits behind a
    Cloudflare JS challenge, so automated collection needs a headless
    browser (Playwright), which is a pending project decision. The
    table ships ahead of that decision because the row shape was
    verified against live API responses. See docs/KNOWN_ISSUES.md.
    """
    __tablename__ = "institutional_flows"
    __table_args__ = (
        UniqueConstraint(
            "date", "dataset", "client_type", "sector_code", "market_type",
            name="uq_flow_row",
            # sector_code is NULL for the market-wide datasets; without
            # this, Postgres would treat those rows as always-distinct
            # and the dedup constraint would be a no-op for them.
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    dataset: Mapped[str] = mapped_column(String(30), nullable=False)
    client_type: Mapped[str] = mapped_column(String(60), nullable=False)
    sector_code: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )  # NCCPL S-codes, e.g. S0005; NULL for market-wide rows
    sector_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    buy_volume: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    buy_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sell_volume: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    sell_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_volume: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    net_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    usd_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="nccpl"
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<InstitutionalFlow {self.date} {self.dataset} "
            f"{self.client_type} {self.market_type} net={self.net_value}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Announcement
# ═══════════════════════════════════════════════════════════════════════════════

class Announcement(Base):
    """
    PSX corporate announcement (earnings results, dividends, board meetings, etc.).
    PDF documents are downloaded and parsed separately — pdf_parsed tracks whether
    the raw_text field has been populated from the PDF.
    """
    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    announced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # QUARTERLY_RESULT, DIVIDEND, BOARD_MEETING, MATERIAL_INFO, OTHER
    pdf_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    pdf_local_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    pdf_parsed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # psx_dps (portal scraper), psx_terminal (mirror), future: pucars
    fiscal_quarter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="announcements")

    def __repr__(self) -> str:
        return f"<Announcement {self.ticker} [{self.category}] {self.title[:50]}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. NewsArticle
# ═══════════════════════════════════════════════════════════════════════════════

class NewsArticle(Base):
    """
    Financial news article from Pakistani media (Dawn, Business Recorder,
    Profit by Pakistan Today). The unique URL constraint ensures we never
    store the same article twice even if multiple RSS feeds reference it.
    """
    __tablename__ = "news_articles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # dawn, brecorder, profit_today
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(
        String(1000), unique=True, nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sentiment_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    word_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="news")

    def __repr__(self) -> str:
        return f"<NewsArticle {self.source}: {self.headline[:50]}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. IntelligenceReport
# ═══════════════════════════════════════════════════════════════════════════════

class IntelligenceReport(Base):
    """
    The crown jewel — a full intelligence report generated by the 4-agent system.
    Contains the ML prediction, conviction score, bull/bear cases, and the raw
    output from each agent for full transparency and audit trail.

    Cost tracking (total_tokens_used, total_cost_usd, generation_time_seconds)
    enables operational monitoring of LLM spend per report.
    """
    __tablename__ = "intelligence_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    ml_beat_probability: Mapped[float] = mapped_column(
        Float, nullable=False
    )  # 0.0 to 1.0
    conviction_score: Mapped[float] = mapped_column(
        Float, nullable=False
    )  # 0.0 to 100.0
    bull_case: Mapped[str] = mapped_column(Text, nullable=False)
    bear_case: Mapped[str] = mapped_column(Text, nullable=False)
    risk_factors: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=list
    )  # List of risk factor strings
    technical_signal: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
    agent_outputs: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )  # Raw outputs from each agent
    # Phase 5 Session 8 — the two deterministic score terms, stored as
    # first-class columns for direct-SQL auditability (the four legacy
    # terms live only in agent_outputs JSON). NULLABLE on purpose:
    # NULL = report predates the terms; 0.0 = term ran, contributed
    # nothing (honest-zero discipline, same as filing_contribution).
    fundamentals_contribution: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # peer-rank value tilt, clamped [-10, +10]
    flow_contribution: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # sector FIPI/LIPI regime, staleness-gated, clamped [-10, +10]
    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    generation_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="reports")
    llm_calls: Mapped[list["LLMCall"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<IntelligenceReport {self.ticker} {self.report_date} "
            f"conviction={self.conviction_score:.1f}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. LLMCall (Observability Audit Table)
# ═══════════════════════════════════════════════════════════════════════════════

class LLMCall(Base):
    """
    Audit log for every LLM API call made through the LLMGateway.
    This is the observability core — every token, every latency measurement,
    every failure is recorded here for cost tracking and debugging.

    analysis_id links calls to the IntelligenceReport they contributed to.
    Calls made outside of report generation (e.g. ad-hoc queries) have
    analysis_id = None.
    """
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intelligence_reports.id"),
        nullable=True,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # SUCCESS, FAILURE, TIMEOUT
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    report: Mapped[Optional["IntelligenceReport"]] = relationship(
        back_populates="llm_calls"
    )

    def __repr__(self) -> str:
        return (
            f"<LLMCall {self.agent_name} [{self.status}] "
            f"{self.prompt_tokens}+{self.completion_tokens} tokens>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Alert
# ═══════════════════════════════════════════════════════════════════════════════

class Alert(Base):
    """
    User-configured alert. Fires when conditions are met during the
    nightly pipeline or on real-time price updates.

    alert_type values:
    - PRICE_ABOVE / PRICE_BELOW: fires when close crosses threshold_value
    - CONVICTION_ABOVE: fires when a new report exceeds threshold_value
    - NEW_REPORT: fires whenever a new intelligence report is generated
    - EARNINGS_ANNOUNCEMENT: fires when a quarterly result is announced
    """
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    alert_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # PRICE_ABOVE, PRICE_BELOW, CONVICTION_ABOVE, NEW_REPORT, EARNINGS_ANNOUNCEMENT
    threshold_value: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_count: Mapped[int] = mapped_column(Integer, default=0)
    last_triggered: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="alerts")
    company: Mapped["Company"] = relationship(back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert {self.alert_type} {self.ticker} threshold={self.threshold_value}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Watchlist
# ═══════════════════════════════════════════════════════════════════════════════

class Watchlist(Base):
    """
    User's watchlist entry. A user can watch multiple tickers, and each
    ticker can be watched by multiple users. The unique constraint
    prevents duplicate entries.
    """
    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("companies.ticker"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="watchlists")
    company: Mapped["Company"] = relationship(back_populates="watchlists")

    def __repr__(self) -> str:
        return f"<Watchlist user={self.user_id} ticker={self.ticker}>"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. PipelineRun (Data Pipeline Observability)
# ═══════════════════════════════════════════════════════════════════════════════

class PipelineRun(Base):
    """
    Tracks execution of data pipelines (scraping, ML training, report generation).
    Enables monitoring dashboards and alerting on pipeline failures.

    status values:
    - RUNNING: pipeline is currently executing
    - SUCCESS: all tickers processed successfully
    - FAILED: pipeline aborted due to critical error
    - PARTIAL: some tickers failed but pipeline completed
    """
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_name: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="RUNNING"
    )  # RUNNING, SUCCESS, FAILED, PARTIAL
    tickers_processed: Mapped[int] = mapped_column(Integer, default=0)
    tickers_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PipelineRun {self.pipeline_name} [{self.status}] "
            f"{self.tickers_processed} processed>"
        )
