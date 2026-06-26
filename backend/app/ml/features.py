"""
PSX Sentinel — ML Feature Engineering (Phase 3 Session 1, extended
Session 3)

Pure-pandas feature pipeline that turns a single ticker's daily_prices
history into either:

    1. A labeled training dataset (batch path, build_features), with
       a 5-trading-day forward label per row. Used by
       scripts/build_ml_dataset.py to write parquet splits for
       offline XGBoost training.

    2. A single point-in-time feature vector for live model inference
       (build_features_point_in_time). Used by app/ml/inference.py
       at agent-run time to score the latest available trading day —
       no label, no forward window required.

Target (batch path only): forward 5-trading-day return, classified as
    UP   if forward_return >  +1%
    DOWN if forward_return <  -1%
    FLAT if |forward_return| <= 1%

All computation is synchronous pandas. The batch path is offline; the
point-in-time path is called from the agent runtime via a thin
inference module — both share `_compute_indicators` so the live
feature vector is identical to what the model was trained on.

Why this exists alongside TrendAnalyzer:
    TrendAnalyzer (app/agents/trend_analyzer.py) computes MA/RSI/etc.
    from a list[dict] of recent prices for HUMAN-facing interpretation
    by an LLM (it shapes pct/round/format text). This module produces
    the ML-model-facing feature vector (raw floats, fractional offsets,
    Wilder-smoothed RSI to match training). Two different consumers,
    two different shapes — kept separate, by design.

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


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add the 11 FEATURE_COLUMNS to `df` in-place-style (returns the same
    frame) without computing the forward label.

    Assumes `df` is already sorted ascending by date and has a numeric
    `close` and `volume` column. Does NOT drop NaN rows — that's the
    caller's responsibility, because the batch and point-in-time paths
    handle "insufficient lookback" differently (batch drops rows,
    point-in-time returns None).

    This is the SINGLE source of truth for the model's feature
    definitions. Both `build_features` (batch / training) and
    `build_features_point_in_time` (live inference) route through here,
    so the vector the production model sees is byte-identical to the
    vector it was trained on.
    """
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

    return df


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

    df = _compute_indicators(df)

    # Forward-looking target — 5 trading days ahead.
    close = df["close"].astype(float)
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


def build_features_point_in_time(
    prices: pd.DataFrame,
) -> dict | None:
    """
    Build a single feature vector for the latest available trading day,
    for live model inference. No label, no forward window required.

    Parameters
    ----------
    prices : pd.DataFrame
        Must contain at minimum columns: date, close, volume.
        Sorted internally; the caller does not need to pre-sort.

    Returns
    -------
    dict | None
        On success:
            {
                "as_of_date": "YYYY-MM-DD",   # date of the latest row
                "close": <float>,             # close on that date
                "features": {<col>: <float>}  # 11 values, order matches
                                              # FEATURE_COLUMNS
            }
        Returns None when there's insufficient trailing history to
        compute every feature (the binding constraint is RANGE_52W =
        252 trading days for position_52w; MA50/return_3m/etc. all
        require less). Returns None instead of padding/imputing — same
        "do not invent data" rule the batch path follows.
    """
    if prices is None or len(prices) == 0:
        return None

    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Fast bail-out before doing any rolling-window math.
    if len(df) < RANGE_52W:
        return None

    df = _compute_indicators(df)

    last = df.iloc[-1]
    feature_values = last[FEATURE_COLUMNS]
    if feature_values.isna().any():
        return None

    return {
        "as_of_date": pd.Timestamp(last["date"]).date().isoformat(),
        "close": float(last["close"]),
        "features": {col: float(feature_values[col]) for col in FEATURE_COLUMNS},
    }
