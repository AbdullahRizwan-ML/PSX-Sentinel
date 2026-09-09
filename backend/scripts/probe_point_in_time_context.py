"""
PSX Sentinel — Phase 6 Session 1: point-in-time context + agent feasibility
probe.

WHAT THIS ANSWERS
------------------
Session 1's job was to build PointInTimeContextBuilder (see
app/backtest/context_builder.py) and then prove — cheaply, before Session
2 commits to a full historical backtest — that the context it builds is
both leakage-safe AND actually usable by the real agent pipeline (real
LLMGateway calls through Groq/Gemini, not mocked). This script is that
proof: 3 tickers x 5 historical dates spread across the existing ML test
window (2025-11-27 -> 2026-07-10, the exact boundaries
backend/scripts/backtest_xgboost.py already validated) = 15 (ticker,
date) pairs. For each pair it builds the as-of context, runs
TrendAnalyzer / NewsSynthesizer / FilingSceptic for real via run_safe(),
and records outputs, latency, and token counts.

WHAT THIS DELIBERATELY DOES NOT DO
------------------------------------
- Does NOT run the Arbitrator or persist an IntelligenceReport. This is a
  probe of the context+agent path, not a scoring run (Session 1 prompt,
  item 3). Every DB write this script makes is an LLMCall audit row —
  nothing else.
- Does NOT touch orchestrator.py / arbitrator.py / trend_analyzer.py /
  news_synthesizer.py / filing_skeptic.py. It imports and calls them
  exactly as they exist today.
- Does NOT create a real IntelligenceReport row, so there is no valid
  analysis_id to attach LLMCall rows to. context.analysis_id is set to
  "" by PointInTimeContextBuilder, which LLMGateway._log_call turns into
  a NULL analysis_id — the same documented pattern the LLMCall model
  uses for "calls made outside of report generation". This script proves
  those rows land by querying llm_calls WHERE analysis_id IS NULL AND
  called_at >= (this run's start time) after the fact, and cross-checks
  the count/tokens against what the AgentResults themselves reported.

USAGE (from backend/ with venv active):
    python scripts/probe_point_in_time_context.py
    python scripts/probe_point_in_time_context.py --save probe_results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# 3 tickers spanning the 3 sectors NCCPL_SECTOR_MAP actually covers
# (Oil & Gas, Banking, Cement) — a deliberate choice so sector_flows
# (built by the context builder for a future Arbitrator-running session,
# even though none of the 3 probed agents read it) is exercised with
# real mapped data on every pair, not left empty by ticker choice.
PROBE_TICKERS = ["PPL", "MCB", "LUCK"]

# Spread across the shared ML test window (2025-11-27 -> 2026-07-10,
# reused from backend/scripts/backtest_xgboost.py / ml_data/test.parquet
# — confirmed live before picking these, not assumed). Deliberately NOT
# cherry-picked for "interesting" dates: a read-only smoke test run
# first (see docs/BUILD_LOG.md, Phase 6 Session 1) showed this spread
# naturally covers both the announcement/news-starved early window and
# the fed later window, which is itself the honest shape of what a full
# Session 2 backtest will encounter — announcements and news are rolling
# mirrors (~10 most recent per ticker / whatever a keyword match caught
# recently), not historical archives.
PROBE_DATES = [
    date(2025, 12, 1),
    date(2026, 2, 1),
    date(2026, 4, 15),
    date(2026, 6, 10),
    date(2026, 7, 8),
]


def _summarize(result) -> dict:
    return {
        "agent_name": result.agent_name,
        "success": result.success,
        "confidence": result.confidence,
        "tokens_used": result.tokens_used,
        "latency_ms": result.latency_ms,
        "error_message": result.error_message,
        "llm_call_made": result.tokens_used > 0,
        "output": result.output,
    }


async def run_one_pair(ticker: str, as_of: date) -> dict:
    from app.agents.filing_skeptic import FilingSceptic
    from app.agents.news_synthesizer import NewsSynthesizer
    from app.agents.trend_analyzer import TrendAnalyzer
    from app.backtest.context_builder import PointInTimeContextBuilder
    from app.core.llm_gateway import LLMGateway
    from app.core.redis_client import redis_client
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        builder = PointInTimeContextBuilder(db)
        pair_start = time.monotonic()
        context, leakage = await builder.build(ticker, as_of)

        llm = LLMGateway(db=db, redis=redis_client)
        trend_agent = TrendAnalyzer(llm=llm, db=db)
        news_agent = NewsSynthesizer(llm=llm, db=db)
        filing_agent = FilingSceptic(llm=llm, db=db)

        trend_result = await trend_agent.run_safe(context)
        news_result = await news_agent.run_safe(context)
        filing_result = await filing_agent.run_safe(context)
        wall_s = time.monotonic() - pair_start

        # Commit per pair (not batched across all 15) so the LLMCall
        # audit rows are durably written incrementally — Phase 5
        # Session 7 hit a Neon idle-timeout on a single end-of-run
        # commit after a long loop; this avoids that class of bug
        # entirely rather than re-discovering it.
        await db.commit()

        return {
            "ticker": ticker,
            "as_of_date": as_of.isoformat(),
            "leakage": leakage,
            "context_stats": {
                "n_prices": len(context.recent_prices),
                "n_news": len(context.news_articles),
                "n_announcements": len(context.announcements),
                "n_flow_days": len(
                    context.sector_flows.get("daily") or []
                ),
                "ml_available": context.ml_signal.get("available"),
                "ml_gate_passed": context.ml_signal.get("gate_passed"),
                "ml_predicted_class": context.ml_signal.get(
                    "predicted_class"
                ),
                "ml_max_prob": context.ml_signal.get("max_prob"),
            },
            "wall_seconds": round(wall_s, 2),
            "agents": {
                "trend_analyzer": _summarize(trend_result),
                "news_synthesizer": _summarize(news_result),
                "filing_skeptic": _summarize(filing_result),
            },
        }


async def dump_llm_calls_since(run_start: datetime) -> list[dict]:
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text(
                "SELECT agent_name, model, status, prompt_tokens, "
                "completion_tokens, latency_ms, called_at FROM llm_calls "
                "WHERE analysis_id IS NULL AND called_at >= :start "
                "ORDER BY called_at ASC"
            ),
            {"start": run_start},
        )
        return [
            {
                "agent_name": row[0],
                "model": row[1],
                "status": row[2],
                "prompt_tokens": row[3],
                "completion_tokens": row[4],
                "latency_ms": row[5],
                "called_at": row[6].isoformat(),
            }
            for row in r.fetchall()
        ]


def print_pair(entry: dict) -> None:
    cs = entry["context_stats"]
    print(
        f"\n{entry['ticker']:<6} as_of={entry['as_of_date']}  "
        f"(leakage all_ok={entry['leakage']['all_ok']}, "
        f"wall={entry['wall_seconds']}s)"
    )
    print(
        f"  context: prices={cs['n_prices']} news={cs['n_news']} "
        f"announcements={cs['n_announcements']} "
        f"flow_days={cs['n_flow_days']}  "
        f"ml: avail={cs['ml_available']} gate={cs['ml_gate_passed']} "
        f"class={cs['ml_predicted_class']} p={cs['ml_max_prob']}"
    )
    for name, a in entry["agents"].items():
        flag = "LLM" if a["llm_call_made"] else "skip"
        print(
            f"  [{flag:4}] {name:<17} success={a['success']} "
            f"conf={a['confidence']:.2f} tokens={a['tokens_used']:4d} "
            f"latency={a['latency_ms']:5d}ms"
            + (f"  ERROR: {a['error_message']}" if a["error_message"] else "")
        )


def print_summary(runs: list[dict], calls: list[dict], total_wall: float) -> None:
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    n_pairs = len(runs)
    leakage_ok = sum(1 for r in runs if r["leakage"]["all_ok"])
    print(f"Pairs run                : {n_pairs}")
    print(f"Leakage checks passed    : {leakage_ok}/{n_pairs}")
    print(f"Total wall-clock (probe) : {total_wall:.1f}s")

    per_agent: dict[str, dict] = {}
    for r in runs:
        for name, a in r["agents"].items():
            d = per_agent.setdefault(
                name,
                {"n_calls": 0, "n_skipped": 0, "n_failed": 0, "tokens": 0,
                 "latency_ms": 0},
            )
            if a["llm_call_made"]:
                d["n_calls"] += 1
                d["tokens"] += a["tokens_used"]
                d["latency_ms"] += a["latency_ms"]
            else:
                d["n_skipped"] += 1
            if not a["success"]:
                d["n_failed"] += 1

    print(
        f"\n{'agent':<17} {'llm_calls':>9} {'skipped':>8} {'failed':>7} "
        f"{'tokens':>8} {'avg_latency_ms':>15}"
    )
    total_calls = 0
    total_tokens = 0
    for name, d in per_agent.items():
        avg_lat = d["latency_ms"] / d["n_calls"] if d["n_calls"] else 0.0
        total_calls += d["n_calls"]
        total_tokens += d["tokens"]
        print(
            f"{name:<17} {d['n_calls']:>9} {d['n_skipped']:>8} "
            f"{d['n_failed']:>7} {d['tokens']:>8} {avg_lat:>15.0f}"
        )
    print(f"{'TOTAL':<17} {total_calls:>9}")

    print(f"\nllm_calls audit rows written : {len(calls)}")
    call_tokens = sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls)
    call_statuses = {}
    for c in calls:
        call_statuses[c["status"]] = call_statuses.get(c["status"], 0) + 1
    print(f"llm_calls token sum          : {call_tokens}")
    print(f"llm_calls status breakdown   : {call_statuses}")
    cross_check_ok = (len(calls) == total_calls) and (call_tokens == total_tokens)
    print(
        f"Cross-check vs AgentResults (n_calls, tokens) MATCH: "
        f"{cross_check_ok}  "
        f"(AgentResult said {total_calls} calls / {total_tokens} tokens; "
        f"llm_calls table has {len(calls)} rows / {call_tokens} tokens. "
        f"Note: Gemini fallback calls report 0 prompt/completion tokens "
        f"at the gateway level — see llm_gateway.py — so a mismatch here "
        f"is expected if any call fell back to Gemini, not a bug.)"
    )

    # ── Cost/time extrapolation ──────────────────────────────────────
    print("\n" + "-" * 78)
    print("EXTRAPOLATION FOR SESSION 2 SIZING")
    print("-" * 78)
    if total_calls > 0:
        per_call_latency_s = sum(
            a["latency_ms"] for r in runs for a in r["agents"].values()
            if a["llm_call_made"]
        ) / 1000.0 / total_calls
    else:
        per_call_latency_s = 0.0
    per_pair_wall_s = total_wall / n_pairs if n_pairs else 0.0
    calls_per_pair = total_calls / n_pairs if n_pairs else 0.0
    tokens_per_call = total_tokens / total_calls if total_calls else 0.0

    max_possible_calls = n_pairs * 3
    print(
        f"This probe (n={n_pairs} pairs, {total_calls} real LLM calls, "
        f"{total_calls / max_possible_calls * 100:.0f}% of the "
        f"max-possible {max_possible_calls} 3-agent calls actually fired):"
    )
    print(f"  wall-clock / pair   : {per_pair_wall_s:.2f}s")
    print(f"  LLM calls / pair    : {calls_per_pair:.2f}  (of 3 possible)")
    print(f"  tokens / real call  : {tokens_per_call:.0f}")
    print(
        "  (TrendAnalyzer always calls the LLM here — full price history "
        "exists for every date in the test window. NewsSynthesizer and "
        "FilingSceptic frequently SKIP for earlier dates: announcements "
        "and news are rolling mirrors, not archives, so most of the test "
        "window predates any announcement/article that still exists in "
        "the DB today. Extrapolated call counts below use THIS probe's "
        "actual skip rate, not an assumed 3-calls-always rate.)"
    )
    print()
    for n_tickers, n_dates, label in [
        (10, 152, "10 tickers x 152 dates (every trading day in the shared test window)"),
        (10, 50, "10 tickers x 50 dates (roughly 1-in-3 sampling of the test window)"),
        (10, 20, "10 tickers x 20 dates (coarse monthly-ish sampling)"),
    ]:
        n = n_tickers * n_dates
        est_wall_s = n * per_pair_wall_s
        est_calls = n * calls_per_pair
        est_tokens = est_calls * tokens_per_call
        print(
            f"  {label:<62} n={n:>5}  "
            f"est. wall-clock={est_wall_s/60:7.1f} min  "
            f"est. LLM calls={est_calls:7.0f}  "
            f"est. tokens={est_tokens:10.0f}"
        )
    print(
        "\nCaveat: this probe's skip rate is specific to PPL/MCB/LUCK and "
        "these 5 dates. A real Session 2 run over more tickers/dates "
        "will skew MORE toward skipped news/filing calls the further "
        "back it samples (the mirror windows are only ~2-3 months deep "
        "as of this session), so these are upper-bound-ish estimates "
        "for LLM cost, not a tight prediction. TrendAnalyzer's call rate "
        "(effectively 100% here) is the reliable floor."
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tickers", default=",".join(PROBE_TICKERS)
    )
    parser.add_argument("--save", help="write a JSON snapshot to this path")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    print("=" * 78)
    print("PSX SENTINEL — Phase 6 Session 1: point-in-time context probe")
    print("=" * 78)
    print(f"Tickers : {tickers}")
    print(f"Dates   : {[d.isoformat() for d in PROBE_DATES]}")
    print(f"Pairs   : {len(tickers) * len(PROBE_DATES)}")
    print(
        "Agents run: trend_analyzer, news_synthesizer, filing_skeptic "
        "(real LLM calls, no mocking). Arbitrator NOT run — no "
        "IntelligenceReport is created this session.\n"
    )

    run_start = datetime.now(timezone.utc)
    probe_start_monotonic = time.monotonic()

    runs = []
    for t in tickers:
        for d in PROBE_DATES:
            entry = await run_one_pair(t, d)
            print_pair(entry)
            runs.append(entry)

    total_wall = time.monotonic() - probe_start_monotonic

    calls = await dump_llm_calls_since(run_start)

    print_summary(runs, calls, total_wall)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "run_start_utc": run_start.isoformat(),
                    "total_wall_seconds": total_wall,
                    "tickers": tickers,
                    "dates": [d.isoformat() for d in PROBE_DATES],
                    "runs": runs,
                    "llm_calls": calls,
                },
                fh,
                indent=2,
            )
        print(f"\nRaw results saved -> {args.save}")


if __name__ == "__main__":
    asyncio.run(main())
