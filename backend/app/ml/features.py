"""
PSX Sentinel — ML Feature Engineering (Phase 3 Session 1)

Pure-pandas batch feature pipeline that turns a single ticker's full
daily_prices history into a labeled dataset for price-direction
prediction.

Target: forward 5-trading-day return, classified as
    UP   if forward_return >  +1%
    DOWN if forward_return <  -1%
    FLAT if |forward_return| <= 1%

This module is OFFLINE / BATCH only. It is not imported by any
FastAPI request path. All computation is synchronous pandas.

Why this exists alongside TrendAnalyzer:
    TrendAnalyzer (app/agents/trend_analyzer.py) computes MA/RSI/etc.
    at a single inference point from a list[dict] of recent prices —
    it's a point-in-time agent run. This module computes the same
    families of indicators across an entire ticker's history,
    vectorised on a DataFrame, for batch ML training. The call
    patterns (point-in-time list vs. full-history frame) are
    different enough that sharing one implementation would add more
    friction than reuse — kept separate, by design.

Judgment calls (documented for posterity):
    - 52-week range position is computed against rolling CLOSE high/
      low, not intraday high/low, because PSX DPS doesn't provide
      real intraday H/L (see docs/KNOWN_ISSUES.md — high/low are
      currently approximated as max/min of open/close).
    - RSI is computed via pandas ewm with alpha=1/period and
      adjust=False. This is the standard Wilder-smoothing recurrence;
      it differs from TrendAnalyzer's SMA-seeded variant only in the
      first ~`period` rows, which are dropped anyway by the lookback
      requirement.
    - Rows with insufficient lookback (early in series) or
      insufficient forward window (last `HORIZON_DAYS` rows) are
      DROPPED, never imputed. Per project rule: no invented data.
    - The binding lookback constraint is the 252-day window for
      `position_52w`. With ~990 raw rows per ticker that leaves
      ~730 labeled rows per ticker.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Target definition
FLAT_THRESHOLD = 0.01      # ±1% defines FLAT
HORIZON_DAYS = 5           # forward window for the label

# Feature lookback windows (trading days)
MA20_WINDOW = 20
MA50_WINDOW = 50
RSI_WINDOW = 14
VOL_WINDOW = 20            # for volume_vs_avg20
VOLATILITY_WINDOW = 20     # for rolling std of daily returns
MOM_1W = 5
MOM_1M = 21
MOM_3M = 63
RANGE_52W = 252            # ~252 trading days in a year

FEATURE_COLUMNS: list[str] = [
    "ma_20",
    "ma_50",
    "price_vs_ma20",
    "price_vs_ma50",
    "rsi_14",
    "return_1w",
    "return_1m",
    "return_3m",
    "volume_vs_avg20",
    "volatility_20d",
    "position_52w",
]


def _rsi_wilder(close: pd.Series, period: int = RSI_WINDOW) -> pd.Series:
    """Wilder-smoothed RSI. NaN until at least `period` rows seen."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(
        alpha=1.0 / period, adjust=False, min_periods=period
    ).mean()
    avg_loss = loss.ewm(
        alpha=1.0 / period, adjust=False, min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # If avg_loss is exactly 0 over the window, RSI is conventionally 100.
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    # Re-apply NaN where we had no smoothed gain at all (pre-warmup).
    rsi = rsi.where(avg_gain.notna(), np.nan)
    return rsi


def _classify(forward_return: float) -> str | None:
    if pd.isna(forward_return):
        return None
    if forward_return > FLAT_THRESHOLD:
        return "UP"
    if forward_return < -FLAT_THRESHOLD:
        return "DOWN"
    return "FLAT"


def build_features(
    prices: pd.DataFrame, ticker: str | None = None
) -> pd.DataFrame:
    """
    Build the labeled feature dataset for one ticker.

    Parameters
    ----------
    prices : pd.DataFrame
        Must contain at minimum columns: date, close, volume.
        Will be sorted by date ascending internally; the caller
        does not need to pre-sort.
    ticker : str | None
        Optional ticker symbol to attach as a column (useful when
        concatenating per-ticker frames downstream).

    Returns
    -------
    pd.DataFrame
        One row per trading day where every feature AND the forward
        label could be computed. Columns:
            date, [ticker,] close,
            ma_20, ma_50, price_vs_ma20, price_vs_ma50,
            rsi_14, return_1w, return_1m, return_3m,
            volume_vs_avg20, volatility_20d, position_52w,
            forward_return_5d, label
    """
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    df["ma_20"] = close.rolling(
        window=MA20_WINDOW, min_periods=MA20_WINDOW
    ).mean()
    df["ma_50"] = close.rolling(
        window=MA50_WINDOW, min_periods=MA50_WINDOW
    ).mean()

    # Fractional offset from MA (e.g. 0.03 == 3% above MA).
    df["price_vs_ma20"] = close / df["ma_20"] - 1.0
    df["price_vs_ma50"] = close / df["ma_50"] - 1.0

    df["rsi_14"] = _rsi_wilder(close, RSI_WINDOW)

    df["return_1w"] = close.pct_change(MOM_1W)
    df["return_1m"] = close.pct_change(MOM_1M)
    df["return_3m"] = close.pct_change(MOM_3M)

    avg_vol_20 = volume.rolling(
        window=VOL_WINDOW, min_periods=VOL_WINDOW
    ).mean()
    df["volume_vs_avg20"] = volume / avg_vol_20.replace(0.0, np.nan)

    daily_return = close.pct_change()
    df["volatility_20d"] = daily_return.rolling(
        window=VOLATILITY_WINDOW, min_periods=VOLATILITY_WINDOW
    ).std()

    rolling_high = close.rolling(
        window=RANGE_52W, min_periods=RANGE_52W
    ).max()
    rolling_low = close.rolling(
        window=RANGE_52W, min_periods=RANGE_52W
    ).min()
    rolling_range = rolling_high - rolling_low
    position = pd.Series(
        np.where(
            rolling_range > 0,
            (close - rolling_low) / rolling_range,
            0.5,  # degenerate case: 252 days at the same price
        ),
        index=close.index,
    )
    # Mask the pre-warmup region so NaN propagates to the dropna below.
    df["position_52w"] = position.where(rolling_high.notna(), np.nan)

    # Forward-looking target — 5 trading days ahead.
    forward_close = close.shift(-HORIZON_DAYS)
    df["forward_return_5d"] = (forward_close - close) / close
    df["label"] = df["forward_return_5d"].apply(_classify)

    if ticker is not None:
        df["ticker"] = ticker

    out_cols = ["date"]
    if ticker is not None:
        out_cols.append("ticker")
    out_cols += ["close"] + FEATURE_COLUMNS + ["forward_return_5d", "label"]
    out = df[out_cols]

    # Drop rows where ANY feature or the label is missing — these
    # are either at the start of the series (insufficient lookback)
    # or in the final HORIZON_DAYS rows (no forward window).
    # Per project rule: drop, do not impute or pad.
    required = FEATURE_COLUMNS + ["forward_return_5d", "label"]
    out = out.dropna(subset=required).reset_index(drop=True)

    return out
