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
from sqlalchemy import select
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
    DailyPrice,
    IntelligenceReport,
    NewsArticle,
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
            ticker, company.name, str(report_id)
        )

        llm = LLMGateway(db=self.db, redis=redis_client)

        trend_agent = TrendAnalyzer(llm=llm, db=self.db)
        news_agent = NewsSynthesizer(llm=llm, db=self.db)
        filing_agent = FilingSceptic(llm=llm, db=self.db)
        arb_agent = Arbitrator(llm=llm, db=self.db)

        logger.info(
            f"Running analysis for {ticker}: "
            f"{len(context.recent_prices)} prices, "
            f"{len(context.news_articles)} articles, "
            f"{len(context.announcements)} announcements"
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
        report.bull_case = arb_output.get(
            "bull_case", "Analysis incomplete."
        )
        report.bear_case = arb_output.get(
            "bear_case", "Analysis incomplete."
        )
        report.risk_factors = arb_output.get("risk_factors", [])
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
        self, ticker: str, company_name: str, analysis_id: str
    ) -> AgentContext:
        cutoff_price = date.today() - timedelta(days=365)
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
            }
            for a in announcements_db
        ]

        return AgentContext(
            ticker=ticker,
            company_name=company_name,
            analysis_id=analysis_id,
            report_date=date.today(),
            recent_prices=recent_prices,
            news_articles=news_articles,
            announcements=announcements,
        )
