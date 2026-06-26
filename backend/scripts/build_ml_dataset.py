"""
PSX Sentinel — ML dataset builder (Phase 3 Session 1)

Pulls all `daily_prices` for every configured ticker from the live
Postgres, runs the feature pipeline per ticker, performs a per-ticker
chronological 70/15/15 train/val/test split, and writes three parquet
files to backend/ml_data/.

This script does NOT train any model. Model training is Phase 3
Session 2 — it will load the parquet files this script produces.

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

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "ml_data"

TRAIN_FRAC = 0.70
VAL_FRAC = 0.15
# test = 1 - TRAIN_FRAC - VAL_FRAC = 0.15


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
    from app.core.config import get_settings
    from app.db.session import AsyncSessionLocal
    from app.ml.features import build_features

    tickers = get_settings().tickers_list
    logger.info(
        f"Building ML dataset for {len(tickers)} tickers: {tickers}"
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    per_ticker_frames: list[pd.DataFrame] = []
    raw_row_total = 0
    dropped_total = 0
    per_ticker_stats: dict[str, dict] = {}

    async with AsyncSessionLocal() as db:
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

            labeled = build_features(prices, ticker=ticker)
            n_labeled = len(labeled)
            dropped_total += n_raw - n_labeled

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
