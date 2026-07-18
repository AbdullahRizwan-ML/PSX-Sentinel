"""
PSX Sentinel — AnalysisOrchestrator

Wires the four specialist agents (TrendAnalyzer, NewsSynthesizer,
FilingSceptic, Arbitrator) into a complete analysis pipeline.

Given a ticker:
1. Loads data from the live database into an AgentContext
2. Runs agents 1-3 sequentially (independent, order doesn't matter)
3. Feeds their results into the Arbitrator for synthesis
4. Persists the final IntelligenceReport to the database
5. Returns structured results with per-agent detail

The IntelligenceReport row is created BEFORE agents run so that
LLMCall audit records can reference it via the analysis_id FK.
"""

import asyncio
import time
import uuid
from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.arbitrator import Arbitrator
from app.agents.base import AgentContext, AgentResult
from app.agents.filing_skeptic import FilingSceptic
from app.agents.news_synthesizer import NewsSynthesizer
from app.agents.trend_analyzer import TrendAnalyzer
from app.core.llm_gateway import LLMGateway
from app.core.redis_client import redis_client
from app.db.models import (
    Announcement,
    Company,
    CompanyFundamentals,
    DailyPrice,
    IntelligenceReport,
    NewsArticle,
)
from app.ml.inference import predict_from_prices

# Calendar-day window for the price pull. The point-in-time ML feature
# builder needs RANGE_52W=252 TRADING days for position_52w; with
# weekends + PSX holidays that maps to ~365 calendar days at the
# margin. We pull 600 to give the latest row real margin (a few months
# of data above the binding constraint), which also lets TrendAnalyzer
# work from a richer-than-before history. Pre-Session-3 this was 365.
PRICE_WINDOW_DAYS = 600

# ── Phase 5 Session 8: sector-flow context (FIPI/LIPI regime term) ──
#
# companies.sector label → NCCPL sector-wise sector_name list. NCCPL's
# finest granularity is sector level (never per-ticker) and its
# vocabulary differs from ours:
#   - Our coarse "Oil & Gas" bucket spans BOTH NCCPL O&G sectors
#     (OGDC/PPL/MARI are Exploration, PSO is Marketing — our sector
#     label can't tell them apart, so both NCCPL sectors are summed;
#     a documented granularity loss, not a bug).
#   - "Investment Companies" (ENGROH) has NO named NCCPL sector — it
#     falls inside NCCPL's "All other Sectors" catch-all, which mixes
#     dozens of unrelated sectors. Mapping it there would fabricate a
#     signal, so it is deliberately UNMAPPED → the Arbitrator emits an
#     honest-zero flow term with reason "sector_not_covered_by_nccpl".
NCCPL_SECTOR_MAP: dict[str, list[str]] = {
    "Banking": ["Commercial Banks"],
    "Cement": ["Cement"],
    "Oil & Gas": [
        "Oil and Gas Exploration Companies",
        "Oil and Gas Marketing Companies",
    ],
}

# Trading-day lookback for the flow window (chosen over 5 in the
# Session 8 exploration: lag-1 autocorrelation 0.94 vs 0.86, sign-flip
# rate ~9% vs ~16% — more regime-like, which is what this term claims
# to measure). The Arbitrator enforces its own minimum-days and
# staleness gates on whatever this fetch returns.
FLOW_LOOKBACK_DAYS = 10

# LIPI client types EXCLUDED from the flow variant. The chosen variant
# is "foreign + local institutional" (FIPI net + LIPI net over every
# non-retail client type), because FIPI + LIPI over ALL client types
# is structurally zero — every foreign net buy is a local net sell by
# accounting identity (verified live on real rows, Session 8) — and
# FIPI-only anti-correlated with forward sector returns for O&G in the
# 2021-2026 exploration while correlating positively for Banks, i.e.
# its sign meaning was sector-inconsistent. Full comparison table in
# docs/BUILD_LOG.md (Session 8).
LIPI_RETAIL_TYPES = ["INDIVIDUALS", "BROKER PROPRIETARY TRADING"]

FLOW_VARIANT = (
    "fipi_plus_local_institutional (REG market, sector-wise datasets; "
    "LIPI minus INDIVIDUALS/BROKER PROPRIETARY TRADING)"
)

# One row per flow trading day for the mapped sectors: net and gross
# (buy + |sell|; sells are stored negative, hence buy - sell) PKR
# turnover of the chosen variant. REGULAR is the pre-2025-05 spelling
# of REG in the FIPI archive rows — both are the cash equities market.
_SECTOR_FLOW_SQL = (
    text(
        """
        SELECT date,
               SUM(CASE
                     WHEN dataset = 'fipi_sector_wise'
                       THEN COALESCE(net_value, 0)
                     WHEN dataset = 'lipi_sector_wise'
                          AND client_type NOT IN :retail
                       THEN COALESCE(net_value, 0)
                     ELSE 0
                   END) AS net_value,
               SUM(CASE
                     WHEN dataset = 'fipi_sector_wise'
                       THEN COALESCE(buy_value, 0) - COALESCE(sell_value, 0)
                     WHEN dataset = 'lipi_sector_wise'
                          AND client_type NOT IN :retail
                       THEN COALESCE(buy_value, 0) - COALESCE(sell_value, 0)
                     ELSE 0
                   END) AS gross_value
        FROM institutional_flows
        WHERE dataset IN ('fipi_sector_wise', 'lipi_sector_wise')
          AND market_type IN ('REG', 'REGULAR')
          AND sector_name IN :sectors
          AND date <= :report_date
        GROUP BY date
        ORDER BY date DESC
        LIMIT :lookback
        """
    )
    .bindparams(
        bindparam("retail", expanding=True),
        bindparam("sectors", expanding=True),
    )
)


class AnalysisOrchestrator:

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(self, ticker: str) -> dict:
        pipeline_start = time.monotonic()
        ticker = ticker.upper()

        company = await self._get_company(ticker)

        report_id = uuid.uuid4()
        report = IntelligenceReport(
            id=report_id,
            ticker=ticker,
            report_date=date.today(),
            ml_beat_probability=0.0,
            conviction_score=50.0,
            bull_case="Analysis in progress...",
            bear_case="Analysis in progress...",
            risk_factors=[],
            technical_signal="NEUTRAL",
            agent_outputs={},
            total_tokens_used=0,
            total_cost_usd=0.0,
            generation_time_seconds=0.0,
        )
        self.db.add(report)
        await self.db.flush()

        context = await self._build_context(
            ticker, company.name, company.sector, str(report_id)
        )

        # Compute the point-in-time ML price-direction signal
        # synchronously. predict_from_prices loads model.json once at
        # module level, then this call is a microsecond numpy op — fine
        # to call inline in the async request path without
        # asyncio.to_thread. It never raises (it returns a structured
        # skip dict on any failure), so we don't wrap in try/except.
        context.ml_signal = predict_from_prices(context.recent_prices)

        llm = LLMGateway(db=self.db, redis=redis_client)

        trend_agent = TrendAnalyzer(llm=llm, db=self.db)
        news_agent = NewsSynthesizer(llm=llm, db=self.db)
        filing_agent = FilingSceptic(llm=llm, db=self.db)
        arb_agent = Arbitrator(llm=llm, db=self.db)

        logger.info(
            f"Running analysis for {ticker}: "
            f"{len(context.recent_prices)} prices, "
            f"{len(context.news_articles)} articles, "
            f"{len(context.announcements)} announcements, "
            f"ml_signal=available={context.ml_signal.get('available')} "
            f"gate={context.ml_signal.get('gate_passed')} "
            f"class={context.ml_signal.get('predicted_class')} "
            f"p={context.ml_signal.get('max_prob')}, "
            f"fundamentals_peers={len(context.peer_fundamentals)}, "
            f"flow_days={len(context.sector_flows.get('daily') or [])} "
            f"(sector='{context.sector_flows.get('sector')}' -> "
            f"{context.sector_flows.get('nccpl_sectors')})"
        )

        trend_result = await trend_agent.run_safe(context)
        news_result = await news_agent.run_safe(context)
        filing_result = await filing_agent.run_safe(context)

        context.trend_signals = {
            **trend_result.output,
            "confidence": trend_result.confidence,
        }
        context.news_sentiment = {
            **news_result.output,
            "confidence": news_result.confidence,
        }
        context.filing_flags = {
            **filing_result.output,
            "confidence": filing_result.confidence,
        }

        arb_result = await arb_agent.run_safe(context)

        all_results = [trend_result, news_result, filing_result, arb_result]
        total_tokens = sum(r.tokens_used for r in all_results)
        generation_time = time.monotonic() - pipeline_start

        arb_output = arb_result.output
        report.conviction_score = arb_output.get("conviction_score", 50.0)
        report.technical_signal = arb_output.get(
            "technical_signal", "NEUTRAL"
        )
        # ml_beat_probability is a legacy field name from the original
        # earnings-beat design (Phase 1A schema, before the target was
        # redefined in Phase 3 Session 1 to 5-day price direction). We
        # repurpose it for the UP-class probability so the existing
        # field stays useful: a number in [0, 1] that means
        # "model-estimated probability of a >+1% move over the next 5
        # trading days." Stays 0.0 when the ML signal is unavailable.
        probs = (context.ml_signal or {}).get("probabilities") or {}
        report.ml_beat_probability = float(probs.get("UP", 0.0))
        report.bull_case = arb_output.get(
            "bull_case", "Analysis incomplete."
        )
        report.bear_case = arb_output.get(
            "bear_case", "Analysis incomplete."
        )
        report.risk_factors = arb_output.get("risk_factors", [])
        # Phase 5 Session 8: the two deterministic terms also land in
        # first-class nullable columns for direct-SQL auditability.
        # dict.get() keeps them None (not 0.0) when the Arbitrator
        # failed outright and produced no breakdown — None means "not
        # computed", 0.0 means "computed, contributed nothing".
        breakdown = arb_output.get("score_breakdown") or {}
        report.fundamentals_contribution = breakdown.get(
            "fundamentals_contribution"
        )
        report.flow_contribution = breakdown.get("flow_contribution")
        report.agent_outputs = {
            r.agent_name: {
                "output": r.output,
                "confidence": r.confidence,
                "success": r.success,
                "tokens_used": r.tokens_used,
                "latency_ms": r.latency_ms,
                "error_message": r.error_message,
            }
            for r in all_results
        }
        report.total_tokens_used = total_tokens
        report.generation_time_seconds = round(generation_time, 2)

        await self.db.flush()

        logger.info(
            f"Analysis complete for {ticker}: "
            f"conviction={report.conviction_score:.1f}, "
            f"signal={report.technical_signal}, "
            f"tokens={total_tokens}, "
            f"time={generation_time:.1f}s"
        )

        return {
            "report": report,
            "agent_results": {
                r.agent_name: {
                    "success": r.success,
                    "confidence": r.confidence,
                    "tokens_used": r.tokens_used,
                    "latency_ms": r.latency_ms,
                    "error_message": r.error_message,
                }
                for r in all_results
            },
        }

    async def _get_company(self, ticker: str) -> Company:
        result = await self.db.execute(
            select(Company).where(Company.ticker == ticker)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise ValueError(f"Company '{ticker}' not found in database")
        return company

    async def _build_context(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        analysis_id: str,
    ) -> AgentContext:
        cutoff_price = date.today() - timedelta(days=PRICE_WINDOW_DAYS)
        price_result = await self.db.execute(
            select(DailyPrice)
            .where(
                DailyPrice.ticker == ticker,
                DailyPrice.date >= cutoff_price,
            )
            .order_by(DailyPrice.date.asc())
        )
        prices = price_result.scalars().all()

        recent_prices = [
            {
                "date": str(p.date),
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "close": p.close,
                "volume": p.volume,
            }
            for p in prices
        ]

        news_cutoff = datetime.combine(
            date.today() - timedelta(days=30), datetime.min.time()
        )
        news_result = await self.db.execute(
            select(NewsArticle)
            .where(
                NewsArticle.ticker == ticker,
                NewsArticle.published_at >= news_cutoff,
            )
            .order_by(NewsArticle.published_at.desc())
        )
        articles = news_result.scalars().all()

        news_articles = [
            {
                "headline": a.headline,
                "summary": a.summary or "",
                "source": a.source,
                "published_at": str(a.published_at),
                "url": a.url,
            }
            for a in articles
        ]

        ann_result = await self.db.execute(
            select(Announcement)
            .where(Announcement.ticker == ticker)
            .order_by(Announcement.announced_at.desc())
        )
        announcements_db = ann_result.scalars().all()

        announcements = [
            {
                "title": a.title,
                "category": a.category,
                "announced_at": str(a.announced_at),
                "raw_text": a.raw_text or "",
                "fiscal_quarter": a.fiscal_quarter,
                "fiscal_year": a.fiscal_year,
                # Phase 5 Session 7: FilingSceptic needs to distinguish
                # "PDF text extracted" from "image-only/absent PDF"
                # (title-only fallback) and to cite the source document.
                "pdf_url": a.pdf_url,
                "pdf_parsed": bool(a.pdf_parsed),
                "source": a.source,
            }
            for a in announcements_db
        ]

        peer_fundamentals = await self._build_peer_fundamentals()
        sector_flows = await self._build_sector_flows(
            sector, date.today()
        )

        return AgentContext(
            ticker=ticker,
            company_name=company_name,
            analysis_id=analysis_id,
            report_date=date.today(),
            recent_prices=recent_prices,
            news_articles=news_articles,
            announcements=announcements,
            peer_fundamentals=peer_fundamentals,
            sector_flows=sector_flows,
        )

    async def _build_peer_fundamentals(self) -> dict:
        """
        Latest fundamentals snapshot for every ACTIVE (non-delisted)
        company that has a company_fundamentals row, keyed by ticker.

        Only the two metrics the Arbitrator's fundamentals tilt ranks
        on are carried (pe_ratio, dividend_yield). Values are passed
        through EXACTLY as stored — including PSX Terminal's
        literal-0.0 dividend yields (documented suspect for LUCK/MARI)
        and NULLs — because deciding what counts as usable data is the
        Arbitrator's job, where the exclusion is logged per ticker in
        the persisted score breakdown.
        """
        result = await self.db.execute(
            select(
                CompanyFundamentals.ticker,
                CompanyFundamentals.pe_ratio,
                CompanyFundamentals.dividend_yield,
            )
            .join(Company, CompanyFundamentals.ticker == Company.ticker)
            .where(Company.delisted_date.is_(None))
        )
        return {
            row.ticker: {
                "pe_ratio": row.pe_ratio,
                "dividend_yield": row.dividend_yield,
            }
            for row in result.all()
        }

    async def _build_sector_flows(
        self, sector: str, report_date: date
    ) -> dict:
        """
        Sector-level NCCPL FIPI/LIPI daily aggregates for the ticker's
        mapped sector(s), for the Arbitrator's flow-regime term.

        Shape:
            {
              "sector":           companies.sector label,
              "nccpl_sectors":    mapped NCCPL sector_name list
                                  ([] when the sector is unmapped —
                                  the Arbitrator turns that into an
                                  honest zero with a logged reason),
              "variant":          human-readable variant definition,
              "latest_flow_date": global MAX(date) in
                                  institutional_flows (None when the
                                  table is empty) — how fresh our flow
                                  archive is at all,
              "daily":            up to FLOW_LOOKBACK_DAYS most recent
                                  flow trading days (ascending), each
                                  {"date", "net_value", "gross_value"}
                                  in PKR for the chosen variant.
            }

        The staleness gate itself lives in the Arbitrator (it is a
        scoring-policy decision and must be visible in the score
        breakdown) — this method only reports the dates honestly.
        """
        mapped = NCCPL_SECTOR_MAP.get(sector, [])

        latest_row = await self.db.execute(
            text("SELECT MAX(date) AS d FROM institutional_flows")
        )
        latest_flow_date = latest_row.scalar_one_or_none()

        daily: list[dict] = []
        if mapped:
            rows = await self.db.execute(
                _SECTOR_FLOW_SQL,
                {
                    "retail": LIPI_RETAIL_TYPES,
                    "sectors": mapped,
                    "report_date": report_date,
                    "lookback": FLOW_LOOKBACK_DAYS,
                },
            )
            daily = [
                {
                    "date": str(r.date),
                    "net_value": float(r.net_value or 0.0),
                    "gross_value": float(r.gross_value or 0.0),
                }
                for r in rows.all()
            ]
            daily.reverse()  # fetched DESC for the LIMIT; serve ascending

        return {
            "sector": sector,
            "nccpl_sectors": mapped,
            "variant": FLOW_VARIANT,
            "latest_flow_date": (
                str(latest_flow_date) if latest_flow_date else None
            ),
            "daily": daily,
        }
