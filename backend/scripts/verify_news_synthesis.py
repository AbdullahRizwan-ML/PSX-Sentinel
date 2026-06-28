"""
Phase 4 Session 5 verification helper.

Confirms that the `news_synthesis` field added to
``IntelligenceReportResponse`` this session is correctly hoisted from
``IntelligenceReport.agent_outputs['news_synthesizer']['output']`` for
every ticker that has a persisted report, AND that the existing
``score_breakdown`` hoist still produces identical results (regression
guard for the validator refactor).

Read-only — no inserts, no updates, no LLM calls.

Expected output for the live DB at session start (2026-06-28):

- PPL: news_synthesis present, article_count=9, relevant_articles=0
       — the "matched but none judged relevant" zero-state.
- MCB: news_synthesis present, article_count=0, relevant_articles=0
       — the "no articles matched at all" zero-state.
- UBL: news_synthesis present, article_count=0, relevant_articles=0
       — same as MCB.
- All three: score_breakdown still hoists identically to the DB
  values (no regression from the validator change).
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


TICKERS = ["PPL", "MCB", "UBL", "HBL", "OGDC", "ENGRO"]


async def main() -> None:
    failures: list[str] = []
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
                print(f"\n=== {ticker}: no report rows (skipped) ===")
                continue

            print(f"\n=== {ticker} ===")
            print(f"id            = {report.id}")
            print(f"generated_at  = {report.generated_at}")
            print(f"conviction    = {report.conviction_score}")

            agent_outputs = report.agent_outputs or {}
            raw_sb = (
                agent_outputs.get("arbitrator", {}).get("output", {})
            ).get("score_breakdown")
            raw_ns = (
                agent_outputs.get("news_synthesizer", {}).get("output")
            )

            try:
                resp = IntelligenceReportResponse.model_validate(report)
            except Exception as exc:
                msg = f"{ticker}: schema validation FAILED: " \
                      f"{type(exc).__name__}: {exc}"
                print(msg)
                failures.append(msg)
                continue

            # Regression check: score_breakdown still hoists identically.
            if raw_sb is None:
                if resp.score_breakdown is not None:
                    msg = (
                        f"{ticker}: schema has score_breakdown but DB "
                        f"doesn't"
                    )
                    failures.append(msg)
                print(f"  score_breakdown: DB=None  schema=None  OK")
            else:
                hoisted_sb = (
                    resp.score_breakdown.model_dump()
                    if resp.score_breakdown
                    else None
                )
                match = (
                    hoisted_sb is not None
                    and hoisted_sb["technical_contribution"]
                    == raw_sb.get("technical_contribution")
                    and hoisted_sb["news_contribution"]
                    == raw_sb.get("news_contribution")
                    and hoisted_sb["filing_contribution"]
                    == raw_sb.get("filing_contribution")
                    and hoisted_sb["ml_contribution"]
                    == raw_sb.get("ml_contribution")
                )
                status = "OK" if match else "FAIL"
                print(f"  score_breakdown regression: {status}")
                if not match:
                    failures.append(
                        f"{ticker}: score_breakdown mismatch — "
                        f"db={raw_sb} schema={hoisted_sb}"
                    )

            # New: news_synthesis hoist.
            print("\n  -- persisted news_synthesizer.output (raw from DB) --")
            print(
                "  " + json.dumps(raw_ns, indent=2, default=str).replace(
                    "\n", "\n  "
                )
            )
            print("\n  -- hoisted news_synthesis (via schema) --")
            hoisted_ns = (
                resp.news_synthesis.model_dump()
                if resp.news_synthesis
                else None
            )
            print(
                "  " + json.dumps(hoisted_ns, indent=2, default=str).replace(
                    "\n", "\n  "
                )
            )

            if raw_ns is None:
                if resp.news_synthesis is not None:
                    msg = (
                        f"{ticker}: schema has news_synthesis but DB "
                        f"doesn't"
                    )
                    failures.append(msg)
                else:
                    print(f"  news_synthesis: DB=None  schema=None  OK")
            else:
                match_ns = (
                    hoisted_ns is not None
                    and hoisted_ns["sentiment"] == raw_ns.get("sentiment")
                    and hoisted_ns["uniformity"] == raw_ns.get("uniformity")
                    and hoisted_ns["article_count"]
                    == raw_ns.get("article_count")
                    and hoisted_ns["relevant_articles"]
                    == raw_ns.get("relevant_articles")
                    and hoisted_ns["narrative_summary"]
                    == raw_ns.get("narrative_summary")
                )
                status = "OK" if match_ns else "FAIL"
                print(f"\n  news_synthesis hoist: {status}")
                if not match_ns:
                    failures.append(
                        f"{ticker}: news_synthesis mismatch — "
                        f"db={raw_ns} schema={hoisted_ns}"
                    )

                # Also classify the zero-state so the frontend logic
                # can be hand-checked against the same classification.
                if raw_ns.get("article_count", 0) == 0:
                    print(
                        f"  zero-state classification: "
                        f"NO_ARTICLES_MATCHED"
                    )
                elif raw_ns.get("relevant_articles", 0) == 0:
                    print(
                        f"  zero-state classification: "
                        f"MATCHED_BUT_NONE_RELEVANT "
                        f"({raw_ns.get('article_count')} matched, 0 relevant)"
                    )
                else:
                    print(
                        f"  zero-state classification: "
                        f"HAS_RELEVANT "
                        f"({raw_ns.get('relevant_articles')} of "
                        f"{raw_ns.get('article_count')} judged relevant)"
                    )

    print()
    if failures:
        print(f"=== {len(failures)} FAILURE(S) ===")
        for msg in failures:
            print(f"  - {msg}")
        sys.exit(1)
    else:
        print("=== All checks passed ===")


if __name__ == "__main__":
    asyncio.run(main())
