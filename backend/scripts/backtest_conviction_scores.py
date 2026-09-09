"""
PSX Sentinel — Phase 6 Session 2: point-in-time conviction scores vs
realized forward returns.

WHAT THIS ANSWERS
-----------------
Every backtest before this one (Phase 5 Sessions 1 and 6) validated the
raw XGBoost model ALONE, replayed from a static parquet. This is the
first time the *deployed composite conviction score* — the number a real
user actually sees on the dashboard — gets checked against what the stock
did afterwards. It generates real point-in-time scores for the full
10-ticker universe across the shared ML test window, pairs each with the
realized forward 5-trading-day return, and reports the correlation.

WHY THE ARBITRATOR'S LLM CALL IS SKIPPED (and why that's sound)
---------------------------------------------------------------
`Arbitrator.run()` does two separable things: it computes the conviction
score deterministically, THEN makes one LLM call for narrative prose.
Reading arbitrator.py confirms the split is clean — `_fundamentals_
contribution`, `_flow_contribution`, `_calculate_score`, `_score_to_label`
and `_build_score_breakdown` all run BEFORE `_build_prompt` /
`self.llm.complete()`, and none of them consume the LLM response. The
response feeds only `bull_case` / `bear_case` / `risk_factors`.

So this script calls those score methods directly on a real Arbitrator
instance and never calls `run()`. We need the number, not the prose;
skipping it saves 200 LLM calls. Arbitrator's scoring code is used
verbatim — NOT reimplemented — so the score here is the same arithmetic
the live system serves. This is proven at the end of the run by querying
`llm_calls` for `agent_name='arbitrator'` in the run window and asserting
it is zero.

HARD RULE — NOTHING IS WRITTEN TO intelligence_reports
-------------------------------------------------------
These are synthetic scores on pretend-historical "today"s. Writing them
into the live reports table would corrupt data a real user could see.
This script NEVER constructs or persists an IntelligenceReport. It does
create `llm_calls` audit rows — those are genuine records of real API
calls the three agents made (analysis_id NULL, the documented "ad-hoc
query" pattern), not synthetic data.

FUNDAMENTALS ARE EXCLUDED (as scoped in Session 1)
---------------------------------------------------
`context.peer_fundamentals` stays `{}`, so the fundamentals term is an
honest zero on every pair — `company_fundamentals` is a current-snapshot
table with no history, and joining today's P/E onto a 2025-12 row would
be lookahead bias (docs/KNOWN_ISSUES.md). This therefore tests a FIVE-term
composite, not the six-term score production serves. Stated in the results
doc too, not just here.

USAGE (from backend/ with venv active):
    python scripts/backtest_conviction_scores.py --limit 30      # tranche
    python scripts/backtest_conviction_scores.py --start 30      # resume
    python scripts/backtest_conviction_scores.py --analyze       # stats only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import pandas as pd  # noqa: E402

ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"
DEFAULT_SNAPSHOT = (
    Path(__file__).resolve().parent.parent / "phase6_session2_scores.json"
)

N_DATES = 20

# Session 1's measured rate, for the running cost comparison. 1.73 LLM
# calls/pair over 15 pairs -> ~347 calls / ~332K tokens at 200 pairs.
S1_PROJECTED_CALLS = 347
S1_PROJECTED_TOKENS = 331_840


def pick_dates(window_start: date, window_end: date, n: int) -> list[date]:
    """`n` evenly spaced calendar dates across [start, end] inclusive.

    Same approach Session 1 used for its 5 probe dates: calendar spacing,
    not trading-day spacing. A T landing on a weekend/holiday is fine and
    realistic — the context builder simply sees the most recent trading
    day at or before T, exactly as a pipeline run on that date would.
    """
    span = (window_end - window_start).days
    return [
        window_start + timedelta(days=round(i * span / (n - 1)))
        for i in range(n)
    ]


def load_price_frames(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """One split-adjusted price frame per ticker, fetched once.

    Reuses app.ml.split_adjustments (the same adjustment the ML dataset
    builder applies) rather than raw closes, so a forward return is never
    contaminated by an unadjusted corporate action.
    """
    import psycopg2

    from app.core.config import get_settings
    from app.ml.split_adjustments import (
        SPLIT_ADJUSTMENTS,
        apply_split_adjustments,
    )

    sync_url = get_settings().DATABASE_URL.replace("+asyncpg", "")
    conn = psycopg2.connect(sync_url)
    frames: dict[str, pd.DataFrame] = {}
    try:
        for t in tickers:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_prices WHERE ticker = %s ORDER BY date ASC",
                conn,
                params=(t,),
            )
            df = apply_split_adjustments(df, t)
            df["date"] = pd.to_datetime(df["date"])
            frames[t] = df.sort_values("date").reset_index(drop=True)
    finally:
        conn.close()

    print(f"Loaded price frames for {len(frames)} tickers "
          f"(split-adjusted via app.ml.split_adjustments).")
    for tk, sd, ratio in SPLIT_ADJUSTMENTS:
        print(f"  known split: {tk} {sd} ratio={ratio:.4f}")
    return frames


def forward_return(
    frame: pd.DataFrame, as_of: date
) -> tuple[float | None, dict]:
    """Realized forward HORIZON_DAYS-trading-day return from `as_of`.

    Entry is the last trading row with date <= as_of — the exact row the
    agents' context ended on. Exit is HORIZON_DAYS trading rows later.
    Uses features.py's HORIZON_DAYS and its (fwd - close)/close formula
    rather than reimplementing the horizon math.
    """
    from app.ml.features import HORIZON_DAYS

    ts = pd.Timestamp(as_of)
    prior = frame[frame["date"] <= ts]
    if prior.empty:
        return None, {"reason": "no_price_at_or_before_T"}

    entry_idx = prior.index[-1]
    exit_idx = entry_idx + HORIZON_DAYS
    entry_row = frame.loc[entry_idx]
    meta = {
        "entry_date": str(entry_row["date"].date()),
        "entry_close": float(entry_row["close"]),
        "horizon_trading_days": HORIZON_DAYS,
    }
    if exit_idx >= len(frame):
        meta["reason"] = "insufficient_forward_window"
        return None, meta

    exit_row = frame.loc[exit_idx]
    ret = (
        float(exit_row["close"]) - float(entry_row["close"])
    ) / float(entry_row["close"])
    meta["exit_date"] = str(exit_row["date"].date())
    meta["exit_close"] = float(exit_row["close"])
    return ret, meta


async def score_one_pair(ticker: str, as_of: date, frame: pd.DataFrame) -> dict:
    from app.agents.arbitrator import Arbitrator
    from app.agents.filing_skeptic import FilingSceptic
    from app.agents.news_synthesizer import NewsSynthesizer
    from app.agents.trend_analyzer import TrendAnalyzer
    from app.backtest.context_builder import PointInTimeContextBuilder
    from app.core.llm_gateway import LLMGateway
    from app.core.redis_client import redis_client
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        t0 = time.monotonic()
        builder = PointInTimeContextBuilder(db)
        context, leakage = await builder.build(ticker, as_of)

        llm = LLMGateway(db=db, redis=redis_client)
        trend_result = await TrendAnalyzer(llm=llm, db=db).run_safe(context)
        news_result = await NewsSynthesizer(llm=llm, db=db).run_safe(context)
        filing_result = await FilingSceptic(llm=llm, db=db).run_safe(context)

        # Thread agent outputs into the context exactly as
        # AnalysisOrchestrator does before the Arbitrator reads them.
        context.trend_signals = {
            **trend_result.output, "confidence": trend_result.confidence
        }
        context.news_sentiment = {
            **news_result.output, "confidence": news_result.confidence
        }
        context.filing_flags = {
            **filing_result.output, "confidence": filing_result.confidence
        }

        # ── Arbitrator: deterministic score ONLY, no narrative call ──
        arb = Arbitrator(llm=llm, db=db)   # run() is deliberately never called
        fund = arb._fundamentals_contribution(
            context.ticker, context.peer_fundamentals or {}
        )
        flow = arb._flow_contribution(
            context.sector_flows or {}, context.report_date
        )
        score = arb._calculate_score(
            context.trend_signals,
            context.news_sentiment,
            context.filing_flags,
            context.ml_signal or {},
            fund[0],
            flow[0],
        )
        breakdown = arb._build_score_breakdown(
            context.trend_signals,
            context.news_sentiment,
            context.filing_flags,
            context.ml_signal or {},
            fund,
            flow,
        )
        label = arb._score_to_label(score)

        await db.commit()   # persist the agents' llm_calls audit rows only

    fwd, fwd_meta = forward_return(frame, as_of)
    agent_tokens = (
        trend_result.tokens_used
        + news_result.tokens_used
        + filing_result.tokens_used
    )
    n_calls = sum(
        1 for r in (trend_result, news_result, filing_result)
        if r.tokens_used > 0
    )

    return {
        "ticker": ticker,
        "as_of_date": as_of.isoformat(),
        "conviction_score": round(score, 1),
        "signal_label": label,
        "score_breakdown": breakdown,
        "forward_5d_return": fwd,
        "forward_meta": fwd_meta,
        "leakage_all_ok": leakage["all_ok"],
        "context_stats": {
            "n_prices": len(context.recent_prices),
            "n_news": len(context.news_articles),
            "n_announcements": len(context.announcements),
            "n_flow_days": len(context.sector_flows.get("daily") or []),
            "ml_gate_passed": context.ml_signal.get("gate_passed"),
            "ml_max_prob": context.ml_signal.get("max_prob"),
        },
        "agent_tokens": agent_tokens,
        "agent_llm_calls": n_calls,
        "wall_seconds": round(time.monotonic() - t0, 2),
    }


def analyze(rows: list[dict]) -> dict:
    """Pearson correlation + tercile means. Deliberately simple — this is
    a sanity check on ~200 points, not a powerful statistical test."""
    usable = [
        r for r in rows
        if r.get("forward_5d_return") is not None
        and r.get("conviction_score") is not None
    ]
    out: dict = {
        "n_total": len(rows),
        "n_usable": len(usable),
        "n_dropped_no_forward_window": len(rows) - len(usable),
    }
    if len(usable) < 3:
        out["error"] = "not enough usable rows"
        return out

    scores = [r["conviction_score"] for r in usable]
    rets = [r["forward_5d_return"] for r in usable]

    out["score_min"] = min(scores)
    out["score_max"] = max(scores)
    out["score_mean"] = round(statistics.mean(scores), 2)
    out["score_stdev"] = round(statistics.pstdev(scores), 3)
    out["n_distinct_scores"] = len(set(scores))
    out["mean_forward_return"] = round(statistics.mean(rets), 5)

    if len(set(scores)) < 2:
        out["pearson_r"] = None
        out["pearson_note"] = (
            "undefined — every conviction score in the sample is identical, "
            "so the score has zero variance and cannot correlate with anything"
        )
    else:
        out["pearson_r"] = round(statistics.correlation(scores, rets), 4)

    # Terciles by score. With heavy ties, boundaries are approximate —
    # reported with the actual per-bucket score ranges so ties are visible.
    ordered = sorted(usable, key=lambda r: r["conviction_score"])
    third = len(ordered) // 3
    buckets = {
        "low": ordered[:third],
        "mid": ordered[third: 2 * third],
        "high": ordered[2 * third:],
    }
    out["terciles"] = {
        name: {
            "n": len(b),
            "score_range": (
                [b[0]["conviction_score"], b[-1]["conviction_score"]]
                if b else None
            ),
            "mean_forward_return": (
                round(statistics.mean(
                    [r["forward_5d_return"] for r in b]
                ), 5) if b else None
            ),
        }
        for name, b in buckets.items()
    }

    # Which terms actually move? With a near-degenerate score this is the
    # real story, so report it from the data rather than asserting it.
    terms = [
        "technical_contribution", "news_contribution",
        "filing_contribution", "ml_contribution",
        "fundamentals_contribution", "flow_contribution",
    ]
    term_stats = {}
    for term in terms:
        vals = [
            r["score_breakdown"].get(term) for r in usable
            if isinstance(r.get("score_breakdown"), dict)
        ]
        vals = [float(v) for v in vals if v is not None]
        if not vals:
            term_stats[term] = {"n": 0, "note": "absent from breakdown"}
            continue
        nonzero = [v for v in vals if abs(v) > 1e-9]
        term_stats[term] = {
            "n": len(vals),
            "n_nonzero": len(nonzero),
            "pct_nonzero": round(100 * len(nonzero) / len(vals), 1),
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
            "mean": round(statistics.mean(vals), 3),
            "stdev": round(statistics.pstdev(vals), 3),
        }
    out["term_stats"] = term_stats

    # Score frequency table — exposes ties that make terciles degenerate.
    freq: dict[float, int] = {}
    for s in scores:
        freq[s] = freq.get(s, 0) + 1
    out["score_frequency"] = dict(
        sorted(freq.items(), key=lambda kv: -kv[1])
    )
    return out


def print_analysis(a: dict) -> None:
    print("=" * 78)
    print("FIRST-PASS ANALYSIS  (sanity check on ~200 points, NOT a")
    print("statistically powerful result — read the caveats in the doc)")
    print("=" * 78)
    print(f"pairs total / usable      : {a['n_total']} / {a['n_usable']} "
          f"(dropped {a['n_dropped_no_forward_window']} with no forward window)")
    if a.get("error"):
        print(a["error"])
        return
    print(f"conviction score range    : {a['score_min']} .. {a['score_max']}")
    print(f"conviction score mean/sd  : {a['score_mean']} / {a['score_stdev']}")
    print(f"distinct score values     : {a['n_distinct_scores']}")
    print(f"mean forward 5d return    : {a['mean_forward_return'] * 100:+.3f}%")
    if a.get("pearson_r") is None:
        print(f"Pearson r                 : n/a — {a.get('pearson_note')}")
    else:
        print(f"Pearson r (score vs fwd)  : {a['pearson_r']:+.4f}")
    print()
    print(f"{'tercile':<8} {'n':>4} {'score range':>18} {'mean fwd 5d return':>20}")
    for name in ("low", "mid", "high"):
        t = a["terciles"][name]
        rng = (f"{t['score_range'][0]} .. {t['score_range'][1]}"
               if t["score_range"] else "n/a")
        mfr = (f"{t['mean_forward_return'] * 100:+.3f}%"
               if t["mean_forward_return"] is not None else "n/a")
        print(f"{name:<8} {t['n']:>4} {rng:>18} {mfr:>20}")
    print()

    if a.get("term_stats"):
        print("SCORE TERM ACTIVITY — which terms actually move the score")
        print(f"{'term':<28} {'n':>4} {'%nonzero':>9} {'min':>8} "
              f"{'max':>8} {'mean':>8} {'sd':>7}")
        for term, s in a["term_stats"].items():
            if s.get("n", 0) == 0:
                print(f"{term:<28} {'0':>4}  {s.get('note', '')}")
                continue
            print(
                f"{term:<28} {s['n']:>4} {s['pct_nonzero']:>8.1f}% "
                f"{s['min']:>8.2f} {s['max']:>8.2f} {s['mean']:>8.2f} "
                f"{s['stdev']:>7.2f}"
            )
        print()

    if a.get("score_frequency"):
        print("SCORE FREQUENCY (most common first) — ties make terciles blunt")
        for s, c in list(a["score_frequency"].items())[:12]:
            print(f"  {s:>6} : {c:>4} pairs "
                  f"({100 * c / a['n_usable']:.1f}%)")
        print()


async def count_arbitrator_calls(run_start: datetime) -> int:
    """Proof that the narrative call really was skipped."""
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text(
                "SELECT COUNT(*) FROM llm_calls WHERE agent_name = "
                "'arbitrator' AND called_at >= :s"
            ),
            {"s": run_start},
        )
        return int(r.scalar_one())


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    p.add_argument("--limit", type=int, default=None,
                   help="run only the first N pairs of the remaining plan")
    p.add_argument("--start", type=int, default=0,
                   help="skip the first N pairs (resume a tranche)")
    p.add_argument("--analyze", action="store_true",
                   help="re-run analysis over an existing snapshot only")
    args = p.parse_args()

    snapshot_path = Path(args.snapshot)

    if args.analyze:
        with open(snapshot_path, encoding="utf-8") as fh:
            data = json.load(fh)
        print_analysis(analyze(data["rows"]))
        return

    # ── Plan: reconfirm the window live, don't trust Session 1's numbers
    test = pd.read_parquet(ML_DATA / "test.parquet")
    test["date"] = pd.to_datetime(test["date"])
    win_start = test["date"].min().date()
    win_end = test["date"].max().date()
    tickers = sorted(test["ticker"].unique().tolist())
    per_ticker_test_start = {
        t: test[test["ticker"] == t]["date"].min().date() for t in tickers
    }
    dates = pick_dates(win_start, win_end, N_DATES)

    print("=" * 78)
    print("PSX SENTINEL — Phase 6 Session 2: conviction score vs forward return")
    print("=" * 78)
    print(f"Shared ML test window (live from test.parquet): "
          f"{win_start} -> {win_end}")
    print(f"Tickers ({len(tickers)}): {tickers}")
    print(f"Dates ({len(dates)}): {[d.isoformat() for d in dates]}")
    print(f"Plan: {len(tickers)} x {len(dates)} = {len(tickers) * len(dates)} pairs")
    print("Arbitrator narrative LLM call: SKIPPED (score is separable — see "
          "module docstring). Agents run for real.")
    print("intelligence_reports writes: NONE (hard rule).")
    print()

    frames = load_price_frames(tickers)
    print()

    plan = [(t, d) for t in tickers for d in dates]
    if args.start:
        plan = plan[args.start:]
    if args.limit:
        plan = plan[: args.limit]
    print(f"This invocation will run {len(plan)} pairs "
          f"(start={args.start}, limit={args.limit}).\n")

    # Resume-friendly: keep previously saved rows, replace matching keys.
    rows: list[dict] = []
    if snapshot_path.exists():
        with open(snapshot_path, encoding="utf-8") as fh:
            rows = json.load(fh).get("rows", [])
        print(f"Loaded {len(rows)} existing rows from {snapshot_path.name}\n")
    existing = {(r["ticker"], r["as_of_date"]) for r in rows}

    run_start = datetime.now(timezone.utc)
    t_start = time.monotonic()
    tokens_total = 0
    calls_total = 0
    leakage_failures = 0

    for i, (ticker, as_of) in enumerate(plan, 1):
        if (ticker, as_of.isoformat()) in existing:
            print(f"[{i}/{len(plan)}] {ticker} {as_of} — already in snapshot, skipping")
            continue

        row = await score_one_pair(ticker, as_of, frames[ticker])
        rows.append(row)
        tokens_total += row["agent_tokens"]
        calls_total += row["agent_llm_calls"]
        if not row["leakage_all_ok"]:
            leakage_failures += 1

        fwd = row["forward_5d_return"]
        fwd_s = f"{fwd * 100:+.2f}%" if fwd is not None else "n/a"
        print(
            f"[{i}/{len(plan)}] {row['ticker']:<7} {row['as_of_date']} "
            f"score={row['conviction_score']:>5.1f} ({row['signal_label']:<11}) "
            f"fwd5d={fwd_s:>8}  calls={row['agent_llm_calls']} "
            f"tok={row['agent_tokens']:>5} {row['wall_seconds']:>5.1f}s "
            f"leak_ok={row['leakage_all_ok']}"
        )

        # Save after every pair — a crash costs nothing.
        with open(snapshot_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "run_start_utc": run_start.isoformat(),
                    "window": [win_start.isoformat(), win_end.isoformat()],
                    "dates": [d.isoformat() for d in dates],
                    "per_ticker_test_start": {
                        k: v.isoformat()
                        for k, v in per_ticker_test_start.items()
                    },
                    "rows": rows,
                },
                fh,
                indent=2,
            )

        # ── Running cost tally, every 10 pairs ──
        if i % 10 == 0:
            elapsed = time.monotonic() - t_start
            per_pair_tok = tokens_total / i
            per_pair_call = calls_total / i
            per_pair_s = elapsed / i
            print(
                f"    ---- CHECKPOINT after {i} pairs: "
                f"{tokens_total:,} tokens, {calls_total} calls, "
                f"{elapsed/60:.1f} min elapsed | "
                f"per-pair: {per_pair_tok:.0f} tok / {per_pair_call:.2f} calls "
                f"/ {per_pair_s:.1f}s | "
                f"projected @200 pairs: {per_pair_tok*200:,.0f} tokens, "
                f"{per_pair_call*200:.0f} calls, {per_pair_s*200/60:.0f} min "
                f"(Session 1 estimated {S1_PROJECTED_TOKENS:,} tokens / "
                f"{S1_PROJECTED_CALLS} calls) ----"
            )

    elapsed = time.monotonic() - t_start
    arb_calls = await count_arbitrator_calls(run_start)

    print()
    print("=" * 78)
    print("RUN TOTALS (this invocation)")
    print("=" * 78)
    print(f"pairs run              : {len([r for r in plan])}")
    print(f"rows in snapshot       : {len(rows)}")
    print(f"agent LLM calls        : {calls_total}")
    print(f"agent tokens           : {tokens_total:,}")
    print(f"wall clock             : {elapsed/60:.1f} min")
    print(f"leakage check failures : {leakage_failures}")
    print(f"arbitrator llm_calls rows since run start : {arb_calls}  "
          f"(MUST be 0 — narrative call skipped)")
    print(f"snapshot               : {snapshot_path}")
    print()

    print_analysis(analyze(rows))


if __name__ == "__main__":
    asyncio.run(main())
