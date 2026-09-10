"""
PSX Sentinel — ML dataset builder (Phase 3 Session 1, extended Phase 7
Session 1)

Pulls all `daily_prices` for every configured ticker from the live
Postgres, runs the feature pipeline per ticker, attaches the sector
institutional-flow features, performs a per-ticker chronological
70/15/15 train/val/test split, and writes three parquet files to
backend/ml_data/.

This script does NOT train any model. `scripts/train_ml_model.py` loads
the parquet files this script produces.

Phase 7 Session 1 addition — sector FIPI/LIPI flow features:
    Two extra feature columns (`sector_flow_ratio`, `flow_available`)
    plus four audit columns are written per row. The math lives in
    app/ml/features.py::attach_flow_features; the DB fetch here reuses
    AnalysisOrchestrator's ALREADY-SETTLED constants and SQL verbatim
    (NCCPL_SECTOR_MAP, FLOW_LOOKBACK_DAYS, LIPI_RETAIL_TYPES,
    _SECTOR_FLOW_SQL) and Arbitrator's gate thresholds (FLOW_MIN_DAYS,
    FLOW_STALE_DAYS) — nothing about the sector mapping or the flow
    variant is re-derived here.

    Efficiency note: fetching the last-10-flow-days window per row
    would be ~10,000 round trips, so the full per-date sector aggregate
    is fetched ONCE per distinct NCCPL sector set (using that same SQL
    with an unbounded LIMIT) and the per-row window is taken from it in
    pandas. That is the same set of rows the per-row query would
    return — verified per-row against the real SQL by
    scripts/verify_flow_features.py, not assumed.

    The 11-column production feature set (features.FEATURE_COLUMNS) is
    UNCHANGED, and so are the labels and the split boundaries: the new
    columns are purely additive, so the existing 11-feature training
    path reads these same parquet files untouched.

Why per-ticker chronological split (not random):
    Adjacent rows share almost all of their feature window (e.g. two
    rows one day apart share 19 of 20 days of MA20 history and 4 of 5
    days of forward window). A random shuffle would leak future data
    into training. A single global cutoff date would also waste a
    ticker's recent rows whenever its history is shorter than another
    ticker's. Per-ticker time-order splitting gives every ticker a
    proportional train/val/test slice while keeping each split
    strictly future-of-the-previous-one within a ticker.

Usage (from the backend/ directory, with .venv active):
    python scripts/build_ml_dataset.py
"""

from __future__ import annotations

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
from loguru import logger  # noqa: E402
from sqlalchemy import select  # noqa: E402

from datetime import date  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "ml_data"

TRAIN_FRAC = 0.70
VAL_FRAC = 0.15
# test = 1 - TRAIN_FRAC - VAL_FRAC = 0.15

# Far-future bound used to pull the FULL sector-flow series through the
# orchestrator's point-in-time SQL (which filters `date <= :report_date`).
FLOW_SERIES_END = date.max
FLOW_SERIES_LIMIT = 10_000_000


async def load_prices_for_ticker(db, ticker: str) -> pd.DataFrame:
    from app.db.models import DailyPrice

    stmt = (
        select(
            DailyPrice.date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
            DailyPrice.volume,
        )
        .where(DailyPrice.ticker == ticker)
        .order_by(DailyPrice.date.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()
    return pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    )


async def load_company_sectors(db) -> dict[str, str]:
    """ticker -> companies.sector label, for the NCCPL sector mapping."""
    from app.db.models import Company

    result = await db.execute(select(Company.ticker, Company.sector))
    return {row.ticker: (row.sector or "") for row in result.all()}


async def load_sector_flow_series(db, nccpl_sectors: list[str]) -> list[dict]:
    """
    Full per-date net/gross aggregate for a set of NCCPL sectors, using
    AnalysisOrchestrator._SECTOR_FLOW_SQL verbatim with an unbounded
    window. Ascending by date.

    Reusing that SQL object (rather than writing a new query) is what
    guarantees the training feature is computed from exactly the same
    variant definition the production Arbitrator term uses: FIPI +
    local-institutional LIPI, REG/REGULAR market, sector-wise datasets,
    retail client types excluded.
    """
    from app.agents.orchestrator import _SECTOR_FLOW_SQL, LIPI_RETAIL_TYPES

    rows = await db.execute(
        _SECTOR_FLOW_SQL,
        {
            "retail": LIPI_RETAIL_TYPES,
            "sectors": nccpl_sectors,
            "report_date": FLOW_SERIES_END,
            "lookback": FLOW_SERIES_LIMIT,
        },
    )
    daily = [
        {
            "date": str(r.date),
            "net_value": float(r.net_value or 0.0),
            "gross_value": float(r.gross_value or 0.0),
        }
        for r in rows.all()
    ]
    daily.reverse()  # SQL orders DESC for its LIMIT; serve ascending
    return daily


def chronological_split(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach a 'split' column with values train/val/test, partitioned
    by row order. Assumes df is already sorted by date ascending.
    Uses integer cutoffs so the boundary is exact and reproducible.
    """
    n = len(df)
    train_end = int(n * TRAIN_FRAC)
    val_end = int(n * (TRAIN_FRAC + VAL_FRAC))
    split = (
        ["train"] * train_end
        + ["val"] * (val_end - train_end)
        + ["test"] * (n - val_end)
    )
    out = df.copy()
    out["split"] = split
    return out


def _fmt_date(value) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if hasattr(value, "date"):
        return str(value.date())
    return str(value)


async def main() -> None:
    from app.agents.arbitrator import Arbitrator
    from app.agents.orchestrator import (
        FLOW_LOOKBACK_DAYS,
        NCCPL_SECTOR_MAP,
    )
    from app.core.config import get_settings
    from app.db.session import AsyncSessionLocal
    from app.ml.features import (
        EXTENDED_FEATURE_COLUMNS,
        FLOW_FEATURE_COLUMNS,
        attach_flow_features,
        build_features,
    )
    from app.ml.split_adjustments import (
        SPLIT_ADJUSTMENTS,
        apply_split_adjustments,
    )

    tickers = get_settings().tickers_list

    if SPLIT_ADJUSTMENTS:
        logger.info(
            "Applying {} split adjustment(s) before feature build:",
            len(SPLIT_ADJUSTMENTS),
        )
        for t, d, r in SPLIT_ADJUSTMENTS:
            logger.info(f"  {t} on {d}: ratio={r:.4f}")
    logger.info(
        f"Building ML dataset for {len(tickers)} tickers: {tickers}"
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    per_ticker_frames: list[pd.DataFrame] = []
    raw_row_total = 0
    dropped_total = 0
    per_ticker_stats: dict[str, dict] = {}
    flow_cache: dict[tuple[str, ...], list[dict]] = {}

    async with AsyncSessionLocal() as db:
        sectors = await load_company_sectors(db)
        logger.info(
            "Flow features: gates min_days={}, stale_days={}, "
            "lookback={} (reused from Arbitrator / orchestrator)",
            Arbitrator.FLOW_MIN_DAYS,
            Arbitrator.FLOW_STALE_DAYS,
            FLOW_LOOKBACK_DAYS,
        )

        for ticker in tickers:
            prices = await load_prices_for_ticker(db, ticker)
            n_raw = len(prices)
            raw_row_total += n_raw

            if n_raw == 0:
                logger.warning(
                    f"{ticker}: 0 raw price rows, skipping"
                )
                per_ticker_stats[ticker] = {
                    "raw": 0,
                    "labeled": 0,
                    "train": 0,
                    "val": 0,
                    "test": 0,
                    "first_date": None,
                    "last_date": None,
                    "train_cut": None,
                    "val_cut": None,
                }
                continue

            prices = apply_split_adjustments(prices, ticker)
            labeled = build_features(prices, ticker=ticker)
            n_labeled = len(labeled)
            dropped_total += n_raw - n_labeled

            # ── Phase 7 Session 1: sector institutional-flow features ──
            sector = sectors.get(ticker, "")
            mapped = NCCPL_SECTOR_MAP.get(sector, [])
            key = tuple(mapped)
            if mapped and key not in flow_cache:
                flow_cache[key] = await load_sector_flow_series(db, mapped)
                logger.info(
                    f"  fetched flow series for {list(mapped)}: "
                    f"{len(flow_cache[key])} flow days"
                )
            labeled = attach_flow_features(
                labeled,
                flow_cache.get(key, []),
                sector_mapped=bool(mapped),
                lookback_days=FLOW_LOOKBACK_DAYS,
                min_days=Arbitrator.FLOW_MIN_DAYS,
                stale_days=Arbitrator.FLOW_STALE_DAYS,
            )
            n_avail = int(labeled["flow_available"].sum())
            logger.info(
                f"  {ticker}: sector='{sector}' -> {list(mapped) or 'UNMAPPED'}, "
                f"flow_available on {n_avail}/{n_labeled} rows"
            )

            split_df = chronological_split(labeled)

            train_part = split_df[split_df["split"] == "train"]
            val_part = split_df[split_df["split"] == "val"]
            test_part = split_df[split_df["split"] == "test"]

            train_cut = (
                train_part["date"].max() if len(train_part) else None
            )
            val_cut = val_part["date"].max() if len(val_part) else None

            per_ticker_stats[ticker] = {
                "raw": n_raw,
                "labeled": n_labeled,
                "train": len(train_part),
                "val": len(val_part),
                "test": len(test_part),
                "first_date": split_df["date"].min()
                if len(split_df) else None,
                "last_date": split_df["date"].max()
                if len(split_df) else None,
                "train_cut": train_cut,
                "val_cut": val_cut,
            }
            per_ticker_frames.append(split_df)

            logger.info(
                f"{ticker}: raw={n_raw}, labeled={n_labeled}, "
                f"train={len(train_part)}/val={len(val_part)}/"
                f"test={len(test_part)}, "
                f"train cut <= {_fmt_date(train_cut)}, "
                f"val cut <= {_fmt_date(val_cut)}"
            )

    if not per_ticker_frames:
        logger.error("No data produced for any ticker. Aborting.")
        return

    full = pd.concat(per_ticker_frames, ignore_index=True)

    train = (
        full[full["split"] == "train"]
        .drop(columns=["split"])
        .reset_index(drop=True)
    )
    val = (
        full[full["split"] == "val"]
        .drop(columns=["split"])
        .reset_index(drop=True)
    )
    test = (
        full[full["split"] == "test"]
        .drop(columns=["split"])
        .reset_index(drop=True)
    )

    train_path = OUTPUT_DIR / "train.parquet"
    val_path = OUTPUT_DIR / "val.parquet"
    test_path = OUTPUT_DIR / "test.parquet"
    train.to_parquet(train_path, index=False)
    val.to_parquet(val_path, index=False)
    test.to_parquet(test_path, index=False)

    print()
    print("=" * 78)
    print("ML DATASET BUILD COMPLETE")
    print("=" * 78)
    print()
    print(f"Output directory: {OUTPUT_DIR}")
    print(
        f"  train.parquet:  {train_path.stat().st_size:>12,} bytes"
    )
    print(
        f"  val.parquet:    {val_path.stat().st_size:>12,} bytes"
    )
    print(
        f"  test.parquet:   {test_path.stat().st_size:>12,} bytes"
    )
    print()
    print(
        f"Raw daily_prices rows read:   {raw_row_total:>7,}"
    )
    print(
        f"Rows dropped (no features):   {dropped_total:>7,}  "
        f"(insufficient lookback or no forward window)"
    )
    print(f"Final labeled rows:           {len(full):>7,}")
    print(f"  Train:                      {len(train):>7,}")
    print(f"  Val:                        {len(val):>7,}")
    print(f"  Test:                       {len(test):>7,}")
    print()

    print("PER-TICKER COUNTS")
    print("-" * 78)
    header = (
        f"{'Ticker':<8} {'Raw':>5} {'Labeled':>8} "
        f"{'Train':>6} {'Val':>5} {'Test':>5} "
        f"{'First date':<12} {'Train cut':<12} {'Val cut':<12}"
    )
    print(header)
    for t, s in per_ticker_stats.items():
        print(
            f"{t:<8} {s['raw']:>5} {s['labeled']:>8} "
            f"{s['train']:>6} {s['val']:>5} {s['test']:>5} "
            f"{_fmt_date(s['first_date']):<12} "
            f"{_fmt_date(s['train_cut']):<12} "
            f"{_fmt_date(s['val_cut']):<12}"
        )
    print()

    print("FLOW FEATURE COVERAGE (Phase 7 Session 1)")
    print("-" * 78)
    print(f"Feature columns now written: {len(EXTENDED_FEATURE_COLUMNS)} "
          f"({len(EXTENDED_FEATURE_COLUMNS) - len(FLOW_FEATURE_COLUMNS)} "
          f"production + {len(FLOW_FEATURE_COLUMNS)} experimental: "
          f"{FLOW_FEATURE_COLUMNS})")
    print()
    print(f"{'Ticker':<8} {'Rows':>6} {'flow_avail':>11} {'%':>6} "
          f"{'mean ratio':>11} {'min':>8} {'max':>8}")
    for t in full["ticker"].unique():
        sub = full[full["ticker"] == t]
        av = sub[sub["flow_available"] == 1.0]
        pct = len(av) / len(sub) * 100.0 if len(sub) else 0.0
        if len(av):
            print(
                f"{t:<8} {len(sub):>6} {len(av):>11} {pct:>5.1f}% "
                f"{av['sector_flow_ratio'].mean():>11.5f} "
                f"{av['sector_flow_ratio'].min():>8.4f} "
                f"{av['sector_flow_ratio'].max():>8.4f}"
            )
        else:
            print(
                f"{t:<8} {len(sub):>6} {len(av):>11} {pct:>5.1f}% "
                f"{'n/a':>11} {'n/a':>8} {'n/a':>8}"
            )
    print()
    print("flow_skip_reason distribution (rows with flow_available = 0):")
    unavail = full[full["flow_available"] == 0.0]
    if len(unavail) == 0:
        print("  (none — every row got a real flow reading)")
    else:
        for reason, n in unavail["flow_skip_reason"].value_counts().items():
            print(f"  {n:>6}  {reason}")
    print()

    # ── Point-in-time proof, on the real rows, not the SQL WHERE clause ──
    print("FLOW FEATURE POINT-IN-TIME CHECK (every row, not a sample)")
    print("-" * 78)
    checked = full[full["flow_window_end"].notna()].copy()
    checked["_we"] = pd.to_datetime(checked["flow_window_end"])
    violations = checked[checked["_we"] > pd.to_datetime(checked["date"])]
    print(f"  rows with a flow window:            {len(checked):>7,}")
    print(f"  rows whose window ends AFTER date:  {len(violations):>7,}")
    if len(violations):
        print("  !! LEAKAGE — sample of violating rows:")
        print(violations[["ticker", "date", "flow_window_end"]].head(10)
              .to_string(index=False))
        raise SystemExit(
            "ABORT: flow feature leaked future data. Dataset NOT trusted."
        )
    print("  [OK] every flow window ends on or before its own row's date")
    print(f"  max staleness observed (days):      "
          f"{checked['flow_staleness_days'].max():>7.0f}  "
          f"(gate: > {Arbitrator.FLOW_STALE_DAYS} => unavailable)")
    print()

    print("CLASS DISTRIBUTION (label = UP / DOWN / FLAT)")
    print("-" * 78)
    for name, frame in [("train", train), ("val", val), ("test", test)]:
        counts = frame["label"].value_counts()
        total = len(frame)
        print(f"  {name} (n={total:,}):")
        for cls in ["UP", "DOWN", "FLAT"]:
            c = int(counts.get(cls, 0))
            pct = (c / total * 100.0) if total > 0 else 0.0
            print(f"    {cls:<5}: {c:>6,}  ({pct:5.1f}%)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
