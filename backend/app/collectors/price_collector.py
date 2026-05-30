"""
PSX Sentinel — Price Data Collector

Collects end-of-day OHLCV price data for PSX tickers using yfinance.

PRIMARY SOURCE: Yahoo Finance with .KA suffix (e.g. "ENGRO.KA")
FALLBACK: If yfinance returns no data for a ticker, logs a warning
          and continues to the next ticker. Never crashes on a single
          ticker failure.

yfinance is synchronous — all calls are wrapped in asyncio.to_thread()
to avoid blocking the async event loop.
"""

import asyncio
import math
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base_collector import BaseCollector
from app.db.models import DailyPrice


class PriceCollector(BaseCollector):
    """
    Downloads 2 years of daily OHLCV data per ticker from Yahoo Finance.

    Yahoo Finance PSX symbols use the .KA suffix (Karachi Stock Exchange).
    Data is inserted with a per-row duplicate check against the unique
    constraint on (ticker, date) to support incremental updates.
    """

    name = "price_collector"

    PSX_SUFFIX = ".KA"
    SLEEP_BETWEEN_TICKERS = 2.0  # seconds — respect Yahoo rate limits
    HISTORY_DAYS = 730  # 2 years of daily data

    async def collect(self, tickers: list[str]) -> dict:
        """
        Fetch last 2 years of daily OHLCV data for each ticker.

        For each ticker:
        1. Build Yahoo Finance symbol: f"{ticker}.KA"
        2. Use asyncio.to_thread to call yfinance in a thread
           (yfinance is synchronous)
        3. Download 2y of daily data using yf.download()
        4. Parse each row into a DailyPrice record
        5. Check UniqueConstraint on (ticker, date) before inserting
        6. Sleep between tickers to respect rate limits

        Returns summary dict with counts.
        """
        processed = 0
        failed = 0
        total_inserted = 0

        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=self.HISTORY_DAYS)

        for ticker in tickers:
            try:
                symbol = f"{ticker}{self.PSX_SUFFIX}"
                logger.info(f"Fetching price data for {symbol}")

                # Run synchronous yfinance in a thread pool
                df = await asyncio.to_thread(
                    self._fetch_yfinance,
                    symbol,
                    start_date.isoformat(),
                    end_date.isoformat(),
                )

                if df is None or df.empty:
                    logger.warning(
                        f"No price data returned for {symbol}. "
                        f"Yahoo Finance may not have data for this ticker."
                    )
                    failed += 1
                    await self.sleep(self.SLEEP_BETWEEN_TICKERS)
                    continue

                # Insert records, skipping duplicates
                inserted = await self._insert_prices(ticker, df)
                total_inserted += inserted
                processed += 1
                logger.info(
                    f"{ticker}: {inserted} price records inserted "
                    f"({len(df)} rows fetched from Yahoo Finance)"
                )

            except Exception as e:
                logger.error(
                    f"Price collection failed for {ticker}: "
                    f"{type(e).__name__}: {e}"
                )
                failed += 1

            await self.sleep(self.SLEEP_BETWEEN_TICKERS)

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": total_inserted,
        }

    def _fetch_yfinance(
        self, symbol: str, start: str, end: str
    ):
        """
        Synchronous yfinance call — runs in thread pool via to_thread().

        Returns a pandas DataFrame with columns: Open, High, Low, Close, Volume.
        Returns None on any error to ensure the caller handles gracefully.

        Uses auto_adjust=True to get adjusted prices that account for
        splits and dividends.
        """
        import yfinance as yf

        try:
            df = yf.download(
                symbol,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                return None

            # Flatten MultiIndex columns if yfinance returns them
            # (happens when downloading a single ticker in newer versions)
            if hasattr(df.columns, "levels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

            return df
        except Exception as e:
            logger.error(f"yfinance error for {symbol}: {e}")
            return None

    async def _insert_prices(self, ticker: str, df) -> int:
        """
        Insert price rows into DailyPrice table.

        Skips rows where (ticker, date) already exists to support
        incremental daily updates without duplicates.

        Handles NaN values gracefully — rows with NaN close price
        are skipped entirely. NaN in other columns defaults to the
        close price (a reasonable fallback for missing OHLC data).

        Returns count of newly inserted rows.
        """
        inserted = 0

        for date_idx, row in df.iterrows():
            try:
                # Extract date from pandas Timestamp index
                price_date = (
                    date_idx.date()
                    if hasattr(date_idx, "date")
                    else date_idx
                )

                # Check if record already exists (unique constraint)
                existing = await self.db.execute(
                    select(DailyPrice).where(
                        DailyPrice.ticker == ticker,
                        DailyPrice.date == price_date,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                # Safe float extraction that handles NaN
                def safe_float(val, default=0.0):
                    try:
                        f = float(val)
                        return default if math.isnan(f) else f
                    except (TypeError, ValueError):
                        return default

                close = safe_float(row.get("Close", row.get("close", 0)))
                open_ = safe_float(
                    row.get("Open", row.get("open", close)), close
                )
                high = safe_float(
                    row.get("High", row.get("high", close)), close
                )
                low = safe_float(
                    row.get("Low", row.get("low", close)), close
                )
                volume = int(
                    safe_float(row.get("Volume", row.get("volume", 0)))
                )

                # Skip rows with no close price — incomplete data
                if close == 0:
                    continue

                price = DailyPrice(
                    ticker=ticker,
                    date=price_date,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
                self.db.add(price)
                inserted += 1

            except Exception as e:
                logger.warning(
                    f"Skipping price row for {ticker} "
                    f"on {date_idx}: {e}"
                )
                continue

        await self.db.commit()
        return inserted
