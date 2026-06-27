"""
Phase 4 Session 2 verification helper.

Pulls the most recent IntelligenceReport per ticker straight from
the live DB, prints the score_breakdown dict that's actually
persisted, and runs the new Pydantic IntelligenceReportResponse
validator against the same ORM row so we can confirm the schema
hoists the same numbers the DB has.

Read-only — no inserts, no updates, no LLM calls.
"""

import asyncio
import json
import sys
from pathlib import Path

# Make app/* importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.models import IntelligenceReport  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.schemas.intelligence import (  # noqa: E402
    IntelligenceReportResponse,
)


TICKERS = ["PPL", "MCB", "UBL", "HBL", "OGDC"]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for ticker in TICKERS:
            result = await db.execute(
                select(IntelligenceReport)
                .where(IntelligenceReport.ticker == ticker)
                .order_by(IntelligenceReport.generated_at.desc())
                .limit(1)
            )
            report = result.scalar_one_or_none()
            if not report:
                print(f"\n=== {ticker}: no report rows ===")
                continue

            print(f"\n=== {ticker} ===")
            print(f"id            = {report.id}")
            print(f"generated_at  = {report.generated_at}")
            print(f"conviction    = {report.conviction_score}")
            print(f"signal        = {report.technical_signal}")
            print(f"ml_beat_prob  = {report.ml_beat_probability}")

            agent_outputs = report.agent_outputs or {}
            arb_out = (
                agent_outputs.get("arbitrator", {}).get("output", {})
            )
            sb = arb_out.get("score_breakdown")
            print("\n-- persisted score_breakdown (raw from DB) --")
            print(json.dumps(sb, indent=2, default=str))

            # Round-trip through the new response schema.
            try:
                resp = IntelligenceReportResponse.model_validate(report)
                hoisted = (
                    resp.score_breakdown.model_dump()
                    if resp.score_breakdown
                    else None
                )
                print("\n-- hoisted score_breakdown (via schema) --")
                print(json.dumps(hoisted, indent=2, default=str))
                match = (
                    hoisted is not None
                    and sb is not None
                    and hoisted["technical_contribution"]
                    == sb["technical_contribution"]
                    and hoisted["news_contribution"] == sb["news_contribution"]
                    and hoisted["filing_contribution"]
                    == sb["filing_contribution"]
                    and hoisted["ml_contribution"] == sb["ml_contribution"]
                )
                print(f"\nCross-check (DB == schema): {match}")
            except Exception as exc:
                print(f"\nSchema validation FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
