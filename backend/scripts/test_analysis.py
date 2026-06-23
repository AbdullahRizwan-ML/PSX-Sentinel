"""
PSX Sentinel — Phase 2B Session 2 verification script.

Runs the AnalysisOrchestrator against real tickers, then queries
the database directly to verify results were persisted.

Usage:
    cd backend
    python scripts/test_analysis.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


async def run_analysis(ticker: str) -> dict:
    from app.agents.orchestrator import AnalysisOrchestrator
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        orchestrator = AnalysisOrchestrator(db)
        result = await orchestrator.analyze(ticker)
        await db.commit()
        return result


async def verify_database():
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        print("\n" + "=" * 60)
        print("DATABASE VERIFICATION")
        print("=" * 60)

        r = await db.execute(text(
            "SELECT ticker, conviction_score, technical_signal, "
            "total_tokens_used, generation_time_seconds, report_date "
            "FROM intelligence_reports ORDER BY generated_at DESC"
        ))
        reports = r.fetchall()
        print(f"\nintelligence_reports: {len(reports)} rows")
        for row in reports:
            print(
                f"  {row[0]}: conviction={row[1]:.1f}, "
                f"signal={row[2]}, tokens={row[3]}, "
                f"time={row[4]:.1f}s, date={row[5]}"
            )

        r = await db.execute(text(
            "SELECT agent_name, model, status, "
            "prompt_tokens, completion_tokens, latency_ms, "
            "analysis_id "
            "FROM llm_calls ORDER BY called_at DESC"
        ))
        calls = r.fetchall()
        print(f"\nllm_calls: {len(calls)} rows")
        for row in calls:
            print(
                f"  {row[0]}: model={row[1]}, status={row[2]}, "
                f"tokens={row[3]}+{row[4]}, latency={row[5]}ms, "
                f"report={str(row[6])[:8]}..."
            )

        r = await db.execute(text(
            "SELECT agent_name, COUNT(*) "
            "FROM llm_calls GROUP BY agent_name ORDER BY agent_name"
        ))
        agent_counts = r.fetchall()
        print(f"\nLLM calls by agent:")
        for row in agent_counts:
            print(f"  {row[0]}: {row[1]} calls")

        r = await db.execute(text(
            "SELECT ir.ticker, lc.agent_name "
            "FROM intelligence_reports ir "
            "LEFT JOIN llm_calls lc ON lc.analysis_id = ir.id "
            "ORDER BY ir.ticker, lc.agent_name"
        ))
        links = r.fetchall()
        print(f"\nReport-to-LLM-call links:")
        for row in links:
            agent = row[1] or "(no LLM call)"
            print(f"  {row[0]} -> {agent}")


async def main():
    tickers = ["PPL", "MCB"]

    for ticker in tickers:
        print(f"\n{'=' * 60}")
        print(f"ANALYZING: {ticker}")
        print("=" * 60)

        result = await run_analysis(ticker)
        report = result["report"]

        print(f"\nConviction score: {report.conviction_score:.1f}")
        print(f"Technical signal: {report.technical_signal}")
        print(f"Tokens used: {report.total_tokens_used}")
        print(f"Time: {report.generation_time_seconds:.1f}s")
        print(f"Bull case: {report.bull_case[:100]}...")
        print(f"Bear case: {report.bear_case[:100]}...")
        print(f"Risk factors: {report.risk_factors}")

        print(f"\nPer-agent breakdown:")
        for name, detail in result["agent_results"].items():
            skipped = detail["tokens_used"] == 0
            print(
                f"  {name}: confidence={detail['confidence']:.2f}, "
                f"tokens={detail['tokens_used']}, "
                f"latency={detail['latency_ms']}ms"
                f"{' [LLM SKIPPED]' if skipped else ''}"
                f"{' ERROR: ' + detail['error_message'] if detail['error_message'] else ''}"
            )

    await verify_database()


if __name__ == "__main__":
    asyncio.run(main())
