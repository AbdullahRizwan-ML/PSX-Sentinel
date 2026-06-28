"""
PSX Sentinel — diagnostic for Phase 4 Session 5 (news article list).

Reports, for every ticker in the seed universe:
  - raw count of rows in `news_articles`
  - latest IntelligenceReport's `agent_outputs["news_synthesizer"]["output"]`
    (sentiment, article_count, relevant_articles, narrative_summary)

Used to determine whether the two zero-states the prompt cares about
("no articles matched at all" vs "articles matched but none judged
relevant") are distinguishable from what the current API surface
already exposes, and whether a backend schema change is needed.

Read-only — no writes, no LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make `app.*` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.db.models import Company, IntelligenceReport, NewsArticle  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        tickers_res = await db.execute(
            select(Company.ticker).order_by(Company.ticker)
        )
        tickers = [t for t, in tickers_res.all()]

        print(f"{'TKR':<6} {'NEWS_ROWS':>9}  LATEST REPORT news_synthesizer.output")
        print("-" * 100)

        for ticker in tickers:
            count_res = await db.execute(
                select(func.count())
                .select_from(NewsArticle)
                .where(NewsArticle.ticker == ticker)
            )
            news_rows = count_res.scalar() or 0

            rep_res = await db.execute(
                select(IntelligenceReport)
                .where(IntelligenceReport.ticker == ticker)
                .order_by(IntelligenceReport.generated_at.desc())
                .limit(1)
            )
            rep = rep_res.scalar_one_or_none()

            if rep is None:
                summary = "no IntelligenceReport"
            else:
                ao = rep.agent_outputs or {}
                ns = (ao.get("news_synthesizer") or {}).get("output") or {}
                summary = json.dumps(
                    {
                        "sentiment": ns.get("sentiment"),
                        "article_count": ns.get("article_count"),
                        "relevant_articles": ns.get("relevant_articles"),
                        "uniformity": ns.get("uniformity"),
                    }
                )

            print(f"{ticker:<6} {news_rows:>9}  {summary}")

        # Also dump 3 sample articles for one ticker that has news.
        sample_ticker = None
        for ticker in tickers:
            r = await db.execute(
                select(func.count())
                .select_from(NewsArticle)
                .where(NewsArticle.ticker == ticker)
            )
            if (r.scalar() or 0) > 0:
                sample_ticker = ticker
                break

        if sample_ticker:
            print()
            print(f"--- Sample articles for {sample_ticker} ---")
            arts_res = await db.execute(
                select(NewsArticle)
                .where(NewsArticle.ticker == sample_ticker)
                .order_by(NewsArticle.published_at.desc())
                .limit(3)
            )
            for art in arts_res.scalars().all():
                print(
                    f"  [{art.published_at.date()}] ({art.source}) "
                    f"{art.headline[:80]}"
                )
                print(f"     url: {art.url}")


if __name__ == "__main__":
    asyncio.run(main())
