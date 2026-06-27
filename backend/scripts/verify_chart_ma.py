"""
Phase 4 Session 3 verification helper.

Read-only diagnostic that prints MA20/MA50 values for the latest
trading day per ticker, computed via the SAME backend code path the
rest of the system uses (app/ml/features.py::_compute_indicators).
Used to hand-check that the new frontend PriceChart's client-side
MA computation matches the system's canonical MA definition (simple
unweighted rolling mean of close, window=20 / 50, min_periods=window).

No DB writes, no LLM calls. Run with:

    python backend/scripts/verify_chart_ma.py [TICKER [TICKER...]]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make app/* importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.models import DailyPrice  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.ml.features import _compute_indicators  # noqa: E402
from app.ml.split_adjustments import apply_split_adjustments  # noqa: E402


DEFAULT_TICKERS = ["PPL", "MCB", "ENGRO"]


async def dump_one(ticker: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
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
        rows = result.all()

    if not rows:
        print(f"\n=== {ticker}: no price rows ===")
        return

    df = pd.DataFrame(
        rows,
        columns=["date", "open", "high", "low", "close", "volume"],
    )
    df["date"] = pd.to_datetime(df["date"])
    # The frontend chart does NOT apply split adjustments — it shows the
    # raw stored prices. Print both for comparison so we know which one
    # to expect when spot-checking the rendered chart's MA values.
    df_raw = df.copy()
    df_raw = _compute_indicators(df_raw)
    df_adj = apply_split_adjustments(df.copy(), ticker)
    df_adj = _compute_indicators(df_adj)

    print(f"\n=== {ticker} (n={len(df)}) ===")
    print(
        f"date range: {df['date'].min().date()} -> "
        f"{df['date'].max().date()}"
    )

    def emit(label: str, frame: pd.DataFrame) -> None:
        tail = frame.tail(3)[
            ["date", "close", "ma_20", "ma_50"]
        ]
        print(f"\n  {label}:")
        for _, r in tail.iterrows():
            d = pd.Timestamp(r["date"]).date().isoformat()
            close = r["close"]
            ma20 = r["ma_20"]
            ma50 = r["ma_50"]
            ma20_s = f"{ma20:>10.4f}" if pd.notna(ma20) else "       N/A"
            ma50_s = f"{ma50:>10.4f}" if pd.notna(ma50) else "       N/A"
            print(
                f"    {d}  close={close:>10.4f}  "
                f"ma20={ma20_s}  ma50={ma50_s}"
            )

    emit("raw stored prices (matches frontend chart)", df_raw)
    emit("split-adjusted (for reference; ML path)", df_adj)


async def main() -> None:
    tickers = [t.upper() for t in (sys.argv[1:] or DEFAULT_TICKERS)]
    for t in tickers:
        await dump_one(t)


if __name__ == "__main__":
    asyncio.run(main())
