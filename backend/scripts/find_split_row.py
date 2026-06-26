"""
PSX Sentinel — One-shot diagnostic for the suspected unadjusted stock-split
row in daily_prices (Phase 3 Session 2, Part 1 — identification step).

Pulls every ticker's full price series from the live DB, computes per-day
close-to-close returns and forward 5-day returns, and prints the most
extreme moves. A row with a ~halving/doubling overnight that has no
matching news/filing is the signature of an unadjusted corporate action
(bonus shares, stock split, right issue).

This script is read-only. Not imported by any application code.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def main() -> None:
    from app.core.config import get_settings
    from app.db.models import DailyPrice
    from app.db.session import AsyncSessionLocal

    tickers = get_settings().tickers_list

    async with AsyncSessionLocal() as db:
        all_frames = []
        for t in tickers:
            stmt = (
                select(
                    DailyPrice.date,
                    DailyPrice.close,
                    DailyPrice.volume,
                )
                .where(DailyPrice.ticker == t)
                .order_by(DailyPrice.date.asc())
            )
            res = await db.execute(stmt)
            df = pd.DataFrame(
                res.all(), columns=["date", "close", "volume"]
            )
            if df.empty:
                continue
            df["ticker"] = t
            df["date"] = pd.to_datetime(df["date"])
            df["ret_1d"] = df["close"].pct_change()
            df["ret_5d_fwd"] = (
                df["close"].shift(-5) - df["close"]
            ) / df["close"]
            df["prev_close"] = df["close"].shift(1)
            df["next5_close"] = df["close"].shift(-5)
            all_frames.append(df)

    full = pd.concat(all_frames, ignore_index=True)

    print("=" * 78)
    print("TOP 15 MOST EXTREME SINGLE-DAY DROPS (ret_1d)")
    print("=" * 78)
    cols = [
        "ticker", "date", "prev_close", "close", "ret_1d", "volume"
    ]
    drops = full.dropna(subset=["ret_1d"]).nsmallest(15, "ret_1d")[cols]
    print(drops.to_string(index=False))

    print()
    print("=" * 78)
    print("TOP 15 MOST EXTREME SINGLE-DAY JUMPS (ret_1d)")
    print("=" * 78)
    jumps = full.dropna(subset=["ret_1d"]).nlargest(15, "ret_1d")[cols]
    print(jumps.to_string(index=False))

    print()
    print("=" * 78)
    print("TOP 15 MOST EXTREME 5-DAY-FORWARD DROPS (ret_5d_fwd)")
    print("=" * 78)
    cols2 = [
        "ticker", "date", "close", "next5_close", "ret_5d_fwd", "volume"
    ]
    fwd_drops = full.dropna(subset=["ret_5d_fwd"]).nsmallest(
        15, "ret_5d_fwd"
    )[cols2]
    print(fwd_drops.to_string(index=False))

    print()
    print("=" * 78)
    print("TOP 15 MOST EXTREME 5-DAY-FORWARD JUMPS (ret_5d_fwd)")
    print("=" * 78)
    fwd_jumps = full.dropna(subset=["ret_5d_fwd"]).nlargest(
        15, "ret_5d_fwd"
    )[cols2]
    print(fwd_jumps.to_string(index=False))


if __name__ == "__main__":
    asyncio.run(main())
