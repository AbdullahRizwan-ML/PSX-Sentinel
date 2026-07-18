"""
PSX Sentinel — Phase 5 Session 8 verification: fundamentals tilt +
FIPI/LIPI flow regime signal wired into the Arbitrator.

Runs the production AnalysisOrchestrator (the exact code path behind
POST /companies/{ticker}/analyze and the Celery run_analysis task) for a
set of real tickers and dumps everything needed to judge the change
honestly:

  - the full score_breakdown (all six contributions once the change is
    in; the four legacy terms before it), pasted verbatim
  - the fundamentals_detail block — proves the suspect-data exclusion
    (LUCK/MARI dividend_yield literal 0.0, ENGROH NULL) actually fired
    and is visible in the persisted output, not silently swallowed
  - the flow_detail block — sector mapping, window, imbalance ratio,
    staleness status
  - the two new intelligence_reports columns
    (fundamentals_contribution / flow_contribution) read back via
    getattr so this script also runs against the PRE-change codebase
    (baseline capture), where they don't exist yet
  - the llm_calls audit rows (gateway-routing regression proof)

--save/--diff support the controlled before/after experiment used in
Phase 5 Session 7: capture a baseline snapshot pre-change, re-run
post-change, and the diff proves the four legacy terms
(technical/news/filing/ml) did not move — only the two new terms
appeared.

WRITES IntelligenceReport rows (it runs real analyses). Real LLM calls.

Usage (from backend/ with venv active):
    python scripts/verify_fundamentals_flow.py --save s8_baseline.json
    ... make the code change ...
    python scripts/verify_fundamentals_flow.py --save s8_after.json
    python scripts/verify_fundamentals_flow.py --diff s8_baseline.json s8_after.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from dotenv import load_dotenv

load_dotenv()

# LUCK + MARI: documented-suspect dividend_yield (literal 0.0 at the
# source) — the exclusion path must fire for them. ENGROH: NULL yield
# at source AND no NCCPL named sector (Investment Companies) — both
# honest-degradation paths. PSO: carried a real filing penalty in
# Session 7, so its filing term is a good isolation regression check.
DEFAULT_TICKERS = ["PPL", "MCB", "LUCK", "MARI", "ENGROH", "PSO"]

LEGACY_TERMS = [
    "technical_contribution",
    "news_contribution",
    "filing_contribution",
    "ml_contribution",
]
NEW_TERMS = ["fundamentals_contribution", "flow_contribution"]


async def run_one(ticker: str) -> dict:
    from app.agents.orchestrator import AnalysisOrchestrator
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        orchestrator = AnalysisOrchestrator(db)
        result = await orchestrator.analyze(ticker)
        await db.commit()
        report = result["report"]
        arb = report.agent_outputs.get("arbitrator", {}).get("output", {})
        return {
            "ticker": ticker,
            "report_id": str(report.id),
            "conviction_score": report.conviction_score,
            "technical_signal": report.technical_signal,
            "score_breakdown": arb.get("score_breakdown", {}),
            # getattr defaults keep this runnable against the
            # pre-change model class (baseline capture).
            "db_fundamentals_contribution": getattr(
                report, "fundamentals_contribution", None
            ),
            "db_flow_contribution": getattr(
                report, "flow_contribution", None
            ),
            "total_tokens": report.total_tokens_used,
            "generation_time_s": report.generation_time_seconds,
        }


async def dump_llm_calls(report_ids: list[str]) -> list[dict]:
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text(
                "SELECT analysis_id, agent_name, model, status, "
                "prompt_tokens, completion_tokens, latency_ms "
                "FROM llm_calls WHERE analysis_id = ANY(:ids) "
                "ORDER BY called_at ASC"
            ),
            {"ids": report_ids},
        )
        return [
            {
                "analysis_id": str(row[0]),
                "agent_name": row[1],
                "model": row[2],
                "status": row[3],
                "prompt_tokens": row[4],
                "completion_tokens": row[5],
                "latency_ms": row[6],
            }
            for row in r.fetchall()
        ]


def print_run(entry: dict) -> None:
    print("=" * 78)
    print(f"{entry['ticker']}  (report {entry['report_id'][:8]}...)")
    print("=" * 78)
    bd = entry["score_breakdown"]
    terms = "  ".join(
        f"{t.replace('_contribution', '')}={bd.get(t)}"
        for t in LEGACY_TERMS + NEW_TERMS
    )
    print(
        f"conviction={entry['conviction_score']:.1f} "
        f"signal={entry['technical_signal']}\n  {terms}"
    )
    print(
        f"  DB columns: fundamentals_contribution="
        f"{entry['db_fundamentals_contribution']} "
        f"flow_contribution={entry['db_flow_contribution']}"
    )

    fd = bd.get("fundamentals_detail")
    if fd:
        print("\n  fundamentals_detail:")
        print(json.dumps(fd, indent=4))
    fl = bd.get("flow_detail")
    if fl:
        print("\n  flow_detail:")
        print(json.dumps(fl, indent=4))
    print()


def diff_snapshots(path_a: str, path_b: str) -> int:
    with open(path_a, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(path_b, encoding="utf-8") as fh:
        b = json.load(fh)
    a_by = {e["ticker"]: e for e in a["runs"]}
    b_by = {e["ticker"]: e for e in b["runs"]}
    print("=" * 78)
    print(f"DIFF  {path_a}  ->  {path_b}")
    print("=" * 78)
    problems = 0
    for t in sorted(set(a_by) & set(b_by)):
        ba = a_by[t]["score_breakdown"]
        bb = b_by[t]["score_breakdown"]
        print(f"\n{t}:")
        for term in LEGACY_TERMS:
            va, vb = ba.get(term), bb.get(term)
            same = va == vb
            if not same:
                problems += 1
            print(
                f"  [{'OK ' if same else 'DIFF'}] {term:<26} {va} -> {vb}"
            )
        for term in NEW_TERMS:
            print(
                f"  [....] {term:<26} {ba.get(term)} -> {bb.get(term)}"
                f"   (new this session)"
            )
        print(
            f"  [....] conviction                 "
            f"{a_by[t]['conviction_score']} -> {b_by[t]['conviction_score']}"
        )
    print(
        f"\n{problems} unexpected legacy-term change(s). "
        "(Small diffs can be legitimate LLM-side variance in trend/news "
        "confidence between runs — judge direction and size, not just "
        "the flag.)"
    )
    return 0


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--save", help="write a JSON snapshot to this path")
    parser.add_argument(
        "--diff", nargs=2, metavar=("BASELINE", "AFTER"),
        help="compare two snapshots instead of running analyses",
    )
    args = parser.parse_args()

    if args.diff:
        raise SystemExit(diff_snapshots(*args.diff))

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    runs = []
    for t in tickers:
        entry = await run_one(t)
        print_run(entry)
        runs.append(entry)

    calls = await dump_llm_calls([r["report_id"] for r in runs])
    print("=" * 78)
    print("LLM CALLS (llm_calls audit table — gateway routing proof)")
    print("=" * 78)
    for c in calls:
        print(
            f"  {c['analysis_id'][:8]}... {c['agent_name']:<18} "
            f"model={c['model']:<28} status={c['status']:<8} "
            f"tokens={c['prompt_tokens']}+{c['completion_tokens']} "
            f"latency={c['latency_ms']}ms"
        )

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump({"runs": runs, "llm_calls": calls}, fh, indent=2)
        print(f"\nSnapshot saved -> {args.save}")


if __name__ == "__main__":
    asyncio.run(main())
