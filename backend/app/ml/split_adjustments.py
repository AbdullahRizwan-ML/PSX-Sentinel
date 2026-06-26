"""
PSX Sentinel — Corporate-action (stock split) backward adjustments
for the offline ML dataset (Phase 3 Session 2).

WHY THIS EXISTS
---------------
The PSX DPS endpoint returns raw close prices that are NOT adjusted for
splits / bonus shares / face-value reductions. An unadjusted split shows
up as a single overnight ~halving (or worse) of the close, which:
    1. Manufactures a fake forward-5-day loss on the rows immediately
       before the split (forward_return_5d crosses the discontinuity).
    2. Corrupts every backward-looking feature for ~252 trading days
       after the split (MA20, MA50, RSI14, return_1m, return_3m,
       volatility_20d, position_52w all look back across the same gap).

Backward-adjusting the pre-split close and volume by the split ratio
restores continuity to the series. This is what real "adjusted close"
data sources do.

HOW WE PICK THE RATIO
---------------------
We use the EMPIRICAL ratio (prev_close / first_post_split_close) rather
than rounding to a clean corporate-action ratio (2:1, 5:1, 10:1, etc.).
Reasoning:

    - Fully data-driven; doesn't depend on an external PSX
      corporate-actions lookup we don't currently have.
    - Guarantees the post-adjustment close series is exactly continuous
      across the split day (return_1d on the split day becomes 0).
    - Cost: any genuine same-day market move on the split day is
      "absorbed" into the adjustment factor. For UBL the empirical
      ratio came out to ~2.01 (so essentially no real move is
      absorbed). For LUCK ~4.79 vs a likely-clean 5:1 implies ~-4% of
      same-day move gets absorbed. For MARI ~8.56 vs a likely-clean
      10:1 implies ~+17% of same-day move gets absorbed. All three are
      acceptable trade-offs for a one-shot dataset prep — substantially
      better than dropping ~252 days of post-split data per ticker.

SPLITS IDENTIFIED (Phase 3 Session 2, find_split_row.py output)
---------------------------------------------------------------
    MARI 2024-09-16   3560.00 -> 415.90   ratio 8.5598   (likely 10:1)
    LUCK 2025-04-28   1748.80 -> 365.00   ratio 4.7912   (likely 5:1)
    UBL  2025-06-23    522.79 -> 259.99   ratio 2.0108   (clean 2:1)

All three are characteristic split signatures: overnight close drops of
50-88% with no corresponding bad news in news_articles, and post-split
trading volumes that produce roughly the same dollar volume as the
pre-split day (consistent with the same money moving more shares).

This module is OFFLINE / BATCH only. It is not imported by any FastAPI
request path.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

# (ticker, split effective date, ratio = pre_close / first_post_close).
# To add a new corporate action: append a row here, then re-run
# scripts/build_ml_dataset.py.
SPLIT_ADJUSTMENTS: list[tuple[str, date, float]] = [
    ("MARI", date(2024, 9, 16), 3560.00 / 415.90),
    ("LUCK", date(2025, 4, 28), 1748.80 / 365.00),
    ("UBL", date(2025, 6, 23), 522.79 / 259.99),
]


def apply_split_adjustments(
    prices: pd.DataFrame, ticker: str
) -> pd.DataFrame:
    """
    Return a copy of `prices` with all pre-split rows back-adjusted
    so the close series is continuous.

    For each split (split_date, ratio) recorded for this ticker:
        - Rows with date <  split_date have close, open, high, low
          divided by ratio.
        - Volume on those same rows is multiplied by ratio (same money,
          more shares post-adjustment).
        - The split day itself and rows after are left untouched.

    Multiple splits for the same ticker are applied in chronological
    order — each pre-split row therefore picks up the cumulative
    adjustment of every split that happened after it.

    Parameters
    ----------
    prices : DataFrame with at least 'date' and 'close' columns.
    ticker : ticker symbol the rows belong to.

    Returns
    -------
    DataFrame with the same columns and row order as the input.
    """
    df = prices.copy()
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])

    ticker_splits = sorted(
        [s for s in SPLIT_ADJUSTMENTS if s[0] == ticker],
        key=lambda s: s[1],
    )
    if not ticker_splits:
        return df

    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(float)
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = df[col].astype(float)

    for _t, split_date, ratio in ticker_splits:
        split_ts = pd.Timestamp(split_date)
        mask = df["date"] < split_ts
        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                df.loc[mask, col] = df.loc[mask, col] / ratio
        if "volume" in df.columns:
            df.loc[mask, "volume"] = df.loc[mask, "volume"] * ratio

    return df
