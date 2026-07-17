"""
PSX Sentinel — Price Data Collector

Collects end-of-day OHLCV price data for PSX tickers from the
PSX Data Portal Service (DPS) timeseries API.

PRIMARY SOURCE: https://dps.psx.com.pk/timeseries/eod/{ticker}
RESPONSE FORMAT: {"status": 1, "data": [[unix_ts, open, volume, close], ...]}

NOTE: The DPS API does not provide High/Low values separately.
      High = max(open, close), Low = min(open, close) as approximations.
      This is acceptable for trend analysis and ML features. Intraday
      highs/lows would require tick-level data from a paid PSX feed.

DEPTH LIMIT (verified live, Phase 5 Session 5, 2026-07-17): the EOD
      endpoint serves a fixed rolling ~5-year window and IGNORES all
      date parameters — from/to, start/end, period, and unix-timestamp
      forms were each tested and all return the identical window
      (2021-07-19 -> today as of the test date). Deeper history does
      not exist at this source. The from/to params below are still sent
      in case DPS ever starts honoring them, but the effective request
      is always "everything the source offers", and rows already in the
      DB that have rolled off the source window are never lost (insert
      is per-row dedup'd, nothing is deleted).

PREVIOUS SOURCE (removed): yfinance with .KA suffix — permanently
      broken due to Cloudflare blocking by Yahoo Finance.
"""

import math
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base_collector import BaseCollector
from app.db.models import DailyPrice


class PriceCollector(BaseCollector):
    """
    Downloads the full available daily EOD history per ticker from PSX DPS.

    The PSX Data Portal Service is the official data source operated
    by the Pakistan Stock Exchange. Data is inserted with a per-row
    duplicate check against the unique constraint on (ticker, date)
    to support incremental updates.
    """

    name = "price_collector"

    DPS_BASE_URL = "https://dps.psx.com.pk/timeseries/eod"
    SLEEP_BETWEEN_TICKERS = 1.0  # PSX DPS is generous with rate limits
    # Request 6 years; DPS ignores this and serves its rolling ~5-year
    # window regardless (see module docstring) — kept wider than the
    # real window so nothing is left unrequested if the param ever
    # starts being honored.
    HISTORY_DAYS = 2190

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://dps.psx.com.pk/",
        "Accept": "application/json, text/javascript, */*",
    }

    async def collect(self, tickers: list[str]) -> dict:
        """
        Fetch the full available daily EOD history for each ticker
        from the PSX DPS timeseries API (a rolling ~5-year window —
        the source ignores requested date ranges, see module docstring).

        For each ticker:
        1. GET https://dps.psx.com.pk/timeseries/eod/{ticker}
        2. Parse JSON response: [unix_ts, open, volume, close]
        3. Derive high = max(open, close), low = min(open, close)
        4. Build DataFrame and insert via _insert_prices()
        5. Sleep between tickers

        Returns summary dict with counts.
        """
        processed = 0
        failed = 0
        total_inserted = 0

        for ticker in tickers:
            try:
                logger.info(f"Fetching price data for {ticker} from PSX DPS")

                df = await self._fetch_psx_dps(ticker)

                if df is None or df.empty:
                    logger.warning(
                        f"No price data returned for {ticker} from PSX DPS."
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
                    f"({len(df)} rows fetched from PSX DPS)"
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

    async def _fetch_psx_dps(self, ticker: str):
        """
        Fetch EOD price data from PSX DPS timeseries API.

        Returns a pandas DataFrame with columns matching what
        _insert_prices() expects: date (as index), open, high,
        low, close, volume, change_pct.

        Returns None on any error.
        """
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=self.HISTORY_DAYS)

        url = f"{self.DPS_BASE_URL}/{ticker}"
        params = {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url, headers=self.HEADERS, params=params
                )

                if resp.status_code != 200:
                    logger.warning(
                        f"PSX DPS {resp.status_code} for {ticker}: "
                        f"{resp.text[:200]}"
                    )
                    return None

                payload = resp.json()

                if payload.get("status") != 1 or not payload.get("data"):
                    logger.warning(
                        f"PSX DPS empty for {ticker}: "
                        f"{payload.get('message', 'no message')}"
                    )
                    return None

                rows = []
                for item in payload["data"]:
                    # item = [unix_timestamp, open, volume, close]
                    if len(item) < 4:
                        continue

                    ts, open_p, volume, close_p = (
                        item[0], item[1], item[2], item[3]
                    )
                    price_date = datetime.fromtimestamp(
                        ts, tz=timezone.utc
                    ).date()
                    open_f = float(open_p)
                    close_f = float(close_p)
                    high_f = max(open_f, close_f)
                    low_f = min(open_f, close_f)
                    vol_i = int(volume)
                    change = round(
                        ((close_f - open_f) / open_f * 100)
                        if open_f > 0
                        else 0.0,
                        4,
                    )
                    rows.append(
                        {
                            "date": price_date,
                            "open": open_f,
                            "high": high_f,
                            "low": low_f,
                            "close": close_f,
                            "volume": vol_i,
                            "change_pct": change,
                        }
                    )

                if not rows:
                    logger.warning(
                        f"PSX DPS: 0 parseable rows for {ticker}"
                    )
                    return None

                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                logger.info(
                    f"PSX DPS: {len(df)} rows for {ticker} "
                    f"({df.index.min().date()} -> {df.index.max().date()})"
                )
                return df

        except Exception as e:
            logger.error(f"PSX DPS fetch failed for {ticker}: {e}")
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
