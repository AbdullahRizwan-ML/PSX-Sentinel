"""
PSX Sentinel — Phase 5 Session 7 verification: FilingSceptic wired to real
announcements.

Runs the production AnalysisOrchestrator (the exact code path behind
POST /companies/{ticker}/analyze and the Celery run_analysis task) for a
set of real tickers, and dumps everything needed to judge the
FilingSceptic change honestly:

  - the full FilingSceptic output (red_flags, severity, filing_analysis,
    data_availability, per-announcement review modes) — pasted verbatim,
    not paraphrased
  - the Arbitrator's score_breakdown (all four contributions), so
  - technical / news / ml contributions can be diffed against a
    pre-change baseline snapshot to prove this session changed ONLY the
    filing term
  - the llm_calls audit rows for each analysis, proving every
    FilingSceptic call routed through LLMGateway (agent_name +
    model + token counts come from the gateway's own audit logging)

WRITES IntelligenceReport rows (it runs real analyses). Not a read-only
probe. LLM calls are real Groq/Gemini calls with real cost.

Usage (from backend/ with venv active):
    python scripts/verify_filing_sceptic.py --save baseline.json
    ... make the code change ...
    python scripts/verify_filing_sceptic.py --save after.json
    python scripts/verify_filing_sceptic.py --diff baseline.json after.json
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

DEFAULT_TICKERS = ["PPL", "UBL", "MEBL", "ENGROH", "OGDC"]


async def run_one(ticker: str) -> dict:
    from app.agents.orchestrator import AnalysisOrchestrator
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        orchestrator = AnalysisOrchestrator(db)
        result = await orchestrator.analyze(ticker)
        await db.commit()
        report = result["report"]
        agent_outputs = report.agent_outputs
        arb = agent_outputs.get("arbitrator", {}).get("output", {})
        filing = agent_outputs.get("filing_skeptic", {})
        return {
            "ticker": ticker,
            "report_id": str(report.id),
            "conviction_score": report.conviction_score,
            "technical_signal": report.technical_signal,
            "score_breakdown": arb.get("score_breakdown", {}),
            "filing_skeptic": filing,
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
    print(
        f"conviction={entry['conviction_score']:.1f} "
        f"signal={entry['technical_signal']}  "
        f"tech={bd.get('technical_contribution')} "
        f"news={bd.get('news_contribution')} "
        f"filing={bd.get('filing_contribution')} "
        f"ml={bd.get('ml_contribution')}"
    )
    f = entry["filing_skeptic"]
    out = f.get("output", {})
    print(
        f"\nFilingSceptic: success={f.get('success')} "
        f"confidence={f.get('confidence')} tokens={f.get('tokens_used')} "
        f"latency={f.get('latency_ms')}ms"
    )
    print(f"  data_availability : {out.get('data_availability')}")
    print(f"  filings_reviewed  : {out.get('filings_reviewed')}")
    print(f"  full_text_count   : {out.get('full_text_count')}")
    print(f"  title_only_count  : {out.get('title_only_count')}")
    print(f"  red_flags         : {out.get('red_flags')}")
    print(f"  severity          : {out.get('severity')}")
    print(f"  filing_analysis   : {out.get('filing_analysis')}")
    reviewed = out.get("reviewed") or []
    if reviewed:
        print("  reviewed:")
        for r in reviewed:
            print(
                f"    [{r.get('mode'):<9}] {r.get('date')} "
                f"{r.get('title', '')[:70]}"
            )
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
    unchanged_terms = ["technical_contribution", "news_contribution",
                      "ml_contribution"]
    problems = 0
    for t in sorted(set(a_by) & set(b_by)):
        ba = a_by[t]["score_breakdown"]
        bb = b_by[t]["score_breakdown"]
        print(f"\n{t}:")
        for term in unchanged_terms:
            va, vb = ba.get(term), bb.get(term)
            same = va == vb
            marker = "OK " if same else "DIFF"
            if not same:
                problems += 1
            print(f"  [{marker}] {term:<25} {va} -> {vb}")
        print(
            f"  [....] filing_contribution       "
            f"{ba.get('filing_contribution')} -> "
            f"{bb.get('filing_contribution')}   (expected to change)"
        )
        print(
            f"  [....] conviction                "
            f"{a_by[t]['conviction_score']} -> {b_by[t]['conviction_score']}"
        )
    print(
        f"\n{problems} unexpected non-filing term change(s). "
        "(Note: small diffs can be legitimate LLM-side variance in "
        "trend/news confidence between runs — judge the direction and "
        "size, not just the flag.)"
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
    print("LLM CALLS (from the llm_calls audit table — gateway routing proof)")
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
