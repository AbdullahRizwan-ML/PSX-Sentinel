"""
PSX Sentinel — flow-feature verification (Phase 7 Session 1).

Read-only. Proves the two experimental flow features written into
ml_data/*.parquet by scripts/build_ml_dataset.py are (a) numerically
identical to what production's own SQL returns for that exact
(ticker, date) pair, and (b) point-in-time safe on the real rows.

Why this exists
---------------
The dataset builder fetches each sector's FULL flow series once and
slices the per-row 10-day window in pandas — ~10,000 per-row SQL round
trips would be unusable. That's a performance shortcut around
AnalysisOrchestrator._SECTOR_FLOW_SQL, and a shortcut is exactly the
kind of thing this project does not take on trust (see CLAUDE.md's
verification convention). So this script re-runs the REAL production
SQL, once per sampled row, with `report_date` set to that row's own
date, recomputes the ratio and the gate decision from scratch, and
demands an exact match against the parquet.

Three checks:
  1. PER-ROW SQL EQUIVALENCE — production SQL vs parquet value, exact
     (a float tolerance of 1e-12 for the division only).
  2. PER-ROW POINT-IN-TIME — every flow date the SQL returned for a row
     is <= that row's date, asserted against the returned rows
     themselves, not inferred from the WHERE clause.
  3. WHOLE-DATASET POINT-IN-TIME — every one of the ~10,000 rows'
     recorded flow_window_end is <= its own date.

Plus a reported limitation: how many DISTINCT flow series actually
exist across the universe (NCCPL granularity is sector-level, so
same-sector tickers necessarily share one series).

Usage (from backend/ with venv active):
    python scripts/verify_flow_features.py            # 60-row sample
    python scripts/verify_flow_features.py --sample 120
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"
TOL = 1e-12


def _load_all() -> pd.DataFrame:
    frames = []
    for name in ["train", "val", "test"]:
        path = ML_DATA / f"{name}.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found - run scripts/build_ml_dataset.py first."
            )
        df = pd.read_parquet(path)
        df["split"] = name
        frames.append(df)
    full = pd.concat(frames, ignore_index=True)
    full["date"] = pd.to_datetime(full["date"])
    return full


async def main(sample_size: int) -> None:
    from app.agents.arbitrator import Arbitrator
    from app.agents.orchestrator import (
        FLOW_LOOKBACK_DAYS,
        LIPI_RETAIL_TYPES,
        NCCPL_SECTOR_MAP,
        _SECTOR_FLOW_SQL,
    )
    from app.db.models import Company
    from app.db.session import AsyncSessionLocal

    full = _load_all()
    required = [
        "sector_flow_ratio",
        "flow_available",
        "flow_skip_reason",
        "flow_window_days",
        "flow_window_end",
        "flow_staleness_days",
    ]
    missing = [c for c in required if c not in full.columns]
    if missing:
        raise SystemExit(
            f"Parquet is missing flow columns {missing} - rebuild the "
            f"dataset with the Phase 7 Session 1 builder first."
        )

    print("=" * 78)
    print("PSX SENTINEL - FLOW FEATURE VERIFICATION (Phase 7 Session 1)")
    print("=" * 78)
    print(f"Dataset rows: {len(full):,}  "
          f"(train {int((full['split'] == 'train').sum()):,} / "
          f"val {int((full['split'] == 'val').sum()):,} / "
          f"test {int((full['split'] == 'test').sum()):,})")
    print(f"Gates reused from production: FLOW_LOOKBACK_DAYS="
          f"{FLOW_LOOKBACK_DAYS}, FLOW_MIN_DAYS="
          f"{Arbitrator.FLOW_MIN_DAYS}, FLOW_STALE_DAYS="
          f"{Arbitrator.FLOW_STALE_DAYS}")
    print()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Company.ticker, Company.sector))
        sectors = {r.ticker: (r.sector or "") for r in result.all()}

        # ── Stratified sample: n rows per ticker, spread across splits ──
        tickers = sorted(full["ticker"].unique())
        per_ticker = max(1, sample_size // len(tickers))
        picks = []
        for t in tickers:
            sub = full[full["ticker"] == t].sort_values("date")
            if len(sub) == 0:
                continue
            step = max(1, len(sub) // per_ticker)
            picks.append(sub.iloc[::step].head(per_ticker))
        sample = pd.concat(picks, ignore_index=True)

        print("-" * 78)
        print(f"CHECK 1+2 - per-row SQL equivalence + point-in-time "
              f"({len(sample)} rows)")
        print("-" * 78)
        print("Each row below re-ran AnalysisOrchestrator._SECTOR_FLOW_SQL "
              "with\nreport_date = that row's own date, then recomputed the "
              "ratio from the\nrows it returned.\n")
        header = (
            f"{'Ticker':<7} {'row date':<11} {'days':>4} "
            f"{'sql window':<24} {'sql ratio':>11} {'parquet':>11} "
            f"{'avail':>6} {'match':>6} {'PIT':>4}"
        )
        print(header)
        print("-" * len(header))

        n_match = 0
        n_pit_ok = 0
        n_fail = 0
        for _, row in sample.iterrows():
            ticker = row["ticker"]
            row_date = row["date"].date()
            mapped = NCCPL_SECTOR_MAP.get(sectors.get(ticker, ""), [])

            if not mapped:
                # Unmapped sector: production would never query at all.
                sql_ratio = 0.0
                sql_avail = 0.0
                window = "(unmapped sector)"
                n_days = 0
                pit_ok = True
            else:
                res = await db.execute(
                    _SECTOR_FLOW_SQL,
                    {
                        "retail": LIPI_RETAIL_TYPES,
                        "sectors": mapped,
                        "report_date": row_date,
                        "lookback": FLOW_LOOKBACK_DAYS,
                    },
                )
                rows = res.all()
                dates = [r.date for r in rows]
                n_days = len(rows)
                # PIT proven against the RETURNED rows, not the SQL text.
                pit_ok = all(d <= row_date for d in dates)
                window = (
                    f"{min(dates)} -> {max(dates)}" if dates else "(empty)"
                )
                net = sum(float(r.net_value or 0.0) for r in rows)
                gross = sum(float(r.gross_value or 0.0) for r in rows)
                staleness = (
                    (row_date - max(dates)).days if dates else None
                )
                if n_days < Arbitrator.FLOW_MIN_DAYS:
                    sql_ratio, sql_avail = 0.0, 0.0
                elif staleness is not None and (
                    staleness > Arbitrator.FLOW_STALE_DAYS
                ):
                    sql_ratio, sql_avail = 0.0, 0.0
                elif gross <= 0:
                    sql_ratio, sql_avail = 0.0, 0.0
                else:
                    sql_ratio, sql_avail = net / gross, 1.0

            pq_ratio = float(row["sector_flow_ratio"])
            pq_avail = float(row["flow_available"])
            match = (
                abs(sql_ratio - pq_ratio) < TOL and sql_avail == pq_avail
            )
            n_match += int(match)
            n_pit_ok += int(pit_ok)
            if not match or not pit_ok:
                n_fail += 1

            print(
                f"{ticker:<7} {str(row_date):<11} {n_days:>4} "
                f"{window:<24} {sql_ratio:>+11.6f} {pq_ratio:>+11.6f} "
                f"{int(pq_avail):>6} {'OK' if match else 'FAIL':>6} "
                f"{'OK' if pit_ok else 'LEAK':>4}"
            )

        print()
        print(f"  SQL-vs-parquet exact match: {n_match}/{len(sample)}")
        print(f"  point-in-time (every returned flow date <= row date): "
              f"{n_pit_ok}/{len(sample)}")
        print()

    # ── Check 3: whole dataset ───────────────────────────────────────────
    print("-" * 78)
    print("CHECK 3 - whole-dataset point-in-time (all rows)")
    print("-" * 78)
    windowed = full[full["flow_window_end"].notna()].copy()
    windowed["_we"] = pd.to_datetime(windowed["flow_window_end"])
    viol = windowed[windowed["_we"] > windowed["date"]]
    print(f"  rows with a flow window:           {len(windowed):>7,}")
    print(f"  windows ending AFTER the row date: {len(viol):>7,}")
    print(f"  max staleness (days):              "
          f"{windowed['flow_staleness_days'].max():>7.0f}")
    print(f"  {'[OK] no leakage' if len(viol) == 0 else '[FAIL] LEAKAGE'}")
    if len(viol):
        n_fail += len(viol)
    print()

    # ── Reported limitation: distinct series count ───────────────────────
    print("-" * 78)
    print("LIMITATION - how many DISTINCT flow series exist?")
    print("-" * 78)
    print("NCCPL's finest granularity is sector level (never per-ticker),")
    print("so same-sector tickers necessarily share one identical series.")
    print()
    pivot = (
        full[full["flow_available"] == 1.0]
        .pivot_table(
            index="date", columns="ticker", values="sector_flow_ratio"
        )
    )
    groups: dict[tuple, list[str]] = {}
    for t in pivot.columns:
        key = tuple(pivot[t].round(10).fillna(-999).tolist())
        groups.setdefault(key, []).append(t)
    print(f"  tickers with a flow reading: {len(pivot.columns)}")
    print(f"  DISTINCT flow series:        {len(groups)}")
    for i, (_, members) in enumerate(groups.items(), 1):
        print(f"    series {i}: {', '.join(sorted(members))}")
    zero_avail = sorted(
        full[full["flow_available"] == 0.0]["ticker"].unique()
    )
    print(f"  tickers with NO flow reading: {zero_avail or 'none'}")
    print()

    print("=" * 78)
    print(
        "ALL FLOW-FEATURE CHECKS PASSED"
        if n_fail == 0
        else f"{n_fail} FLOW-FEATURE CHECK(S) FAILED"
    )
    print("=" * 78)
    if n_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sample",
        type=int,
        default=60,
        help="approx. total rows to re-verify via live SQL (default 60)",
    )
    args = ap.parse_args()
    asyncio.run(main(args.sample))
