"""
PSX Sentinel — Point-in-time AgentContext builder (Phase 6 Session 1)

Every backtest so far (Phase 5 Sessions 1 and 6) validated the raw XGBoost
model in isolation, replayed from a static parquet file. Nobody has ever
checked whether the deployed conviction score — all Arbitrator terms
combined, as a real user would see it — actually correlates with forward
returns, because nothing could reconstruct what the four agents would have
seen if the pipeline had run on an arbitrary historical date T instead of
today. This module is that reconstruction.

It is a PARALLEL path to AnalysisOrchestrator._build_context, not a
replacement or a rewrite of it. AnalysisOrchestrator answers "what does
the context look like right now"; PointInTimeContextBuilder answers "what
would the context have looked like on date T" — same AgentContext shape,
same DB tables, different (and stricter) date bounds. orchestrator.py is
untouched this session; several of its already-correct, already-verified
constants (PRICE_WINDOW_DAYS, the NCCPL sector mapping, the flow-window
SQL) are imported and reused as-is rather than re-derived, so the two
paths cannot silently drift apart on those decisions.

FUNDAMENTALS ARE DELIBERATELY NOT POPULATED (Phase 6 Session 1 decision)
-------------------------------------------------------------------------
context.peer_fundamentals is always left at its AgentContext default
({}). docs/KNOWN_ISSUES.md ("PSX Terminal fundamentals are a current
snapshot — lookahead-bias risk for ML features", Phase 5 Session 5) is
explicit that `company_fundamentals` holds exactly one live-snapshot row
per ticker with no history and no as-of dates beyond `scraped_at` —
today's P/E joined onto a 2025-12-01 backtest row would leak information
from after that row's date. A point-in-time fundamentals feature is
possible in principle (PSX Terminal's `fyReports` payload carries 20
fiscal years with per-FY `earnings_release_date`), but building and
verifying that is a separate, larger piece of work, not a blocker for
this session's context-construction infrastructure. With
peer_fundamentals == {}, Arbitrator._fundamentals_contribution already
has a defined, honest path for "no context provided" — it returns 0.0
with a logged skip reason, the same honest-zero discipline documented
for every other term. This is a known, accepted limitation of any
backtest built on top of this module, not a silent gap — see
docs/BUILD_LOG.md, Phase 6 Session 1, and do NOT mark the KNOWN_ISSUES
entry resolved; this module works around the problem, it doesn't fix it.

LEAKAGE DISCIPLINE
-------------------
Every query below has its upper date bound applied in SQL (WHERE ... <=
:as_of_date). That alone would be enough to trust in principle, but this
project's rule (see backend/scripts/backtest_xgboost.py's
assert_no_leakage) is to never just claim safety — prove it against the
real rows returned. `PointInTimeContextBuilder.build()` therefore
re-checks every row it pulled in Python after the fact and returns a
leakage report alongside the context; ContextLeakageError is raised
immediately if any single row violates its bound, exactly like
backtest_xgboost.py raising RuntimeError on a leakage failure.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.orchestrator import (
    FLOW_LOOKBACK_DAYS,
    LIPI_RETAIL_TYPES,
    NCCPL_SECTOR_MAP,
    PRICE_WINDOW_DAYS,
    _SECTOR_FLOW_SQL,
)
from app.db.models import Announcement, Company, DailyPrice, NewsArticle
from app.ml.inference import predict_from_prices

# Mirrors the inline `timedelta(days=30)` orchestrator.py uses for its
# news lookback. Not imported (orchestrator doesn't name it as a
# constant) — kept here as one so this module's own window is at least
# self-documenting; the VALUE is the thing being matched, not any logic.
NEWS_WINDOW_DAYS = 30


class ContextLeakageError(RuntimeError):
    """Raised when a row with date > as_of_date was pulled into a
    supposedly point-in-time context. Should never fire if the SQL
    bounds below are correct — this is the proof, not the mechanism."""


class PointInTimeContextBuilder:
    """
    Builds an AgentContext "as of" an arbitrary historical date T for one
    ticker, using only rows that would have existed in the database on
    that date. Same shape as AnalysisOrchestrator._build_context's
    output; see the module docstring for how the two relate.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(
        self, ticker: str, as_of_date: date
    ) -> tuple[AgentContext, dict]:
        ticker = ticker.upper()
        company = await self._get_company(ticker)

        prices, price_check = await self._prices_as_of(ticker, as_of_date)
        news, news_check = await self._news_as_of(ticker, as_of_date)
        announcements, ann_check = await self._announcements_as_of(
            ticker, as_of_date
        )
        sector_flows, flow_check = await self._sector_flows_as_of(
            company.sector, as_of_date
        )

        context = AgentContext(
            ticker=ticker,
            company_name=company.name,
            # Empty string, not a synthetic UUID: LLMGateway._log_call
            # does `uuid.UUID(analysis_id) if analysis_id else None` —
            # a non-UUID string would raise inside that call's own
            # try/except and be swallowed, silently producing ZERO
            # llm_calls audit rows (the opposite of what this probe
            # needs to prove). Empty string -> None -> analysis_id
            # column stays NULL, the documented pattern for "calls made
            # outside of report generation" per LLMCall's own docstring.
            analysis_id="",
            report_date=as_of_date,
            recent_prices=prices,
            news_articles=news,
            announcements=announcements,
            peer_fundamentals={},  # deliberately excluded — see module docstring
            sector_flows=sector_flows,
        )
        # build_features_point_in_time (app/ml/features.py, Phase 5
        # Session 6) is already point-in-time safe by construction — it
        # only ever looks at rows it's handed, and context.recent_prices
        # is already bounded to date <= as_of_date above. Reused via
        # predict_from_prices exactly as the live orchestrator uses it,
        # not reimplemented.
        context.ml_signal = predict_from_prices(context.recent_prices)

        leakage_report = {
            "ticker": ticker,
            "as_of_date": as_of_date.isoformat(),
            "checks": {
                "prices": price_check,
                "news_articles": news_check,
                "announcements": ann_check,
                "institutional_flows": flow_check,
            },
        }
        all_ok = all(
            c["ok"] for c in leakage_report["checks"].values()
        )
        leakage_report["all_ok"] = all_ok

        if all_ok:
            logger.info(
                f"[leakage-check OK] {ticker} as_of={as_of_date} "
                f"prices<= {price_check['max_date']} "
                f"news<= {news_check['max_date']} "
                f"announcements<= {ann_check['max_date']} "
                f"flows<= {flow_check['max_date']}"
            )
        else:
            logger.error(
                f"[leakage-check FAILED] {ticker} as_of={as_of_date}: "
                f"{leakage_report['checks']}"
            )
            raise ContextLeakageError(
                f"Point-in-time leakage detected for {ticker} as of "
                f"{as_of_date}: {leakage_report['checks']}"
            )

        return context, leakage_report

    async def _get_company(self, ticker: str) -> Company:
        result = await self.db.execute(
            select(Company).where(Company.ticker == ticker)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise ValueError(f"Company '{ticker}' not found in database")
        return company

    async def _prices_as_of(
        self, ticker: str, as_of_date: date
    ) -> tuple[list[dict], dict]:
        cutoff = as_of_date - timedelta(days=PRICE_WINDOW_DAYS)
        result = await self.db.execute(
            select(DailyPrice)
            .where(
                DailyPrice.ticker == ticker,
                DailyPrice.date >= cutoff,
                DailyPrice.date <= as_of_date,
            )
            .order_by(DailyPrice.date.asc())
        )
        rows = result.scalars().all()

        prices = [
            {
                "date": str(p.date),
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "close": p.close,
                "volume": p.volume,
            }
            for p in rows
        ]
        dates = [p.date for p in rows]
        check = self._date_check(dates, as_of_date)
        return prices, check

    async def _news_as_of(
        self, ticker: str, as_of_date: date
    ) -> tuple[list[dict], dict]:
        low = datetime.combine(
            as_of_date - timedelta(days=NEWS_WINDOW_DAYS), time.min
        )
        high = datetime.combine(as_of_date, time.max)
        result = await self.db.execute(
            select(NewsArticle)
            .where(
                NewsArticle.ticker == ticker,
                NewsArticle.published_at >= low,
                NewsArticle.published_at <= high,
            )
            .order_by(NewsArticle.published_at.desc())
        )
        rows = result.scalars().all()

        articles = [
            {
                "headline": a.headline,
                "summary": a.summary or "",
                "source": a.source,
                "published_at": str(a.published_at),
                "url": a.url,
            }
            for a in rows
        ]
        dates = [a.published_at.date() for a in rows]
        check = self._date_check(dates, as_of_date)
        return articles, check

    async def _announcements_as_of(
        self, ticker: str, as_of_date: date
    ) -> tuple[list[dict], dict]:
        high = datetime.combine(as_of_date, time.max)
        result = await self.db.execute(
            select(Announcement)
            .where(
                Announcement.ticker == ticker,
                Announcement.announced_at <= high,
            )
            .order_by(Announcement.announced_at.desc())
        )
        rows = result.scalars().all()

        announcements = [
            {
                "title": a.title,
                "category": a.category,
                "announced_at": str(a.announced_at),
                "raw_text": a.raw_text or "",
                "fiscal_quarter": a.fiscal_quarter,
                "fiscal_year": a.fiscal_year,
                "pdf_url": a.pdf_url,
                "pdf_parsed": bool(a.pdf_parsed),
                "source": a.source,
            }
            for a in rows
        ]
        dates = [a.announced_at.date() for a in rows]
        check = self._date_check(dates, as_of_date)
        return announcements, check

    async def _sector_flows_as_of(
        self, sector: str, as_of_date: date
    ) -> tuple[dict, dict]:
        """
        Same shape and SQL as AnalysisOrchestrator._build_sector_flows,
        reused via its module-level constants rather than duplicated —
        that SQL already parameterizes on `date <= :report_date`, so
        passing as_of_date instead of today makes it point-in-time safe
        for free. Not consumed by any of Session 1's three probed
        agents (only the Arbitrator reads sector_flows), but built here
        so the context this module produces is already complete for a
        future session that DOES run the Arbitrator.
        """
        mapped = NCCPL_SECTOR_MAP.get(sector, [])

        daily: list[dict] = []
        dates: list[date] = []
        if mapped:
            rows = await self.db.execute(
                _SECTOR_FLOW_SQL,
                {
                    "retail": LIPI_RETAIL_TYPES,
                    "sectors": mapped,
                    "report_date": as_of_date,
                    "lookback": FLOW_LOOKBACK_DAYS,
                },
            )
            fetched = rows.all()
            dates = [r.date for r in fetched]
            daily = [
                {
                    "date": str(r.date),
                    "net_value": float(r.net_value or 0.0),
                    "gross_value": float(r.gross_value or 0.0),
                }
                for r in fetched
            ]
            daily.reverse()  # fetched DESC for the LIMIT; serve ascending

        sector_flows = {
            "sector": sector,
            "nccpl_sectors": mapped,
            "variant": "fipi_plus_local_institutional (as-of-T)",
            "latest_flow_date": (
                max(str(d) for d in dates) if dates else None
            ),
            "daily": daily,
        }
        check = self._date_check(dates, as_of_date)
        return sector_flows, check

    @staticmethod
    def _date_check(dates: list[date], as_of_date: date) -> dict:
        if not dates:
            return {"n": 0, "min_date": None, "max_date": None, "ok": True}
        return {
            "n": len(dates),
            "min_date": min(dates).isoformat(),
            "max_date": max(dates).isoformat(),
            "ok": all(d <= as_of_date for d in dates),
        }
