"""
PSX Sentinel — Phase 3 Session 3 read-only ML probe.

Hits the live DB to pull recent prices for every ticker and runs
predict_from_prices over each one — no LLM calls, no DB writes. Used
to pick which tickers to feed through the full orchestrator for live
verification.

Usage:
    cd backend
    python scripts/probe_ml_signal.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from sqlalchemy import select

    from app.agents.orchestrator import PRICE_WINDOW_DAYS
    from app.db.models import Company, DailyPrice
    from app.db.session import AsyncSessionLocal
    from app.ml.inference import predict_from_prices

    cutoff = date.today() - timedelta(days=PRICE_WINDOW_DAYS)

    async with AsyncSessionLocal() as db:
        cres = await db.execute(select(Company).order_by(Company.ticker))
        companies = cres.scalars().all()

        print(
            f"{'TICKER':<7} {'ROWS':>5} {'CLASS':>5} {'PROB':>6} "
            f"{'GATE':>6}  REASON / AS_OF"
        )
        print("-" * 78)

        for c in companies:
            r = await db.execute(
                select(DailyPrice)
                .where(
                    DailyPrice.ticker == c.ticker,
                    DailyPrice.date >= cutoff,
                )
                .order_by(DailyPrice.date.asc())
            )
            prices = r.scalars().all()
            rows = [
                {
                    "date": str(p.date),
                    "close": p.close,
                    "volume": p.volume,
                }
                for p in prices
            ]
            sig = predict_from_prices(rows)
            cls = sig.get("predicted_class") or "-"
            prob = sig.get("max_prob")
            prob_str = f"{prob:.3f}" if prob is not None else "  -  "
            gate = "PASS" if sig.get("gate_passed") else "fail"
            reason = sig.get("skip_reason") or "ok"
            as_of = sig.get("as_of_date") or "-"
            print(
                f"{c.ticker:<7} {len(rows):>5d} {cls:>5} {prob_str:>6} "
                f"{gate:>6}  {reason}  {as_of}"
            )


if __name__ == "__main__":
    asyncio.run(main())
