"""
PSX Sentinel — Fundamentals Collector

Collects company fundamentals (P/E, dividend yield, market cap, free
float) and mirrors PSX corporate announcements, both from PSX Terminal
(psxterminal.com) via app/integrations/psx_terminal_client.py.

PRIMARY SOURCE: psxterminal.com (free, no-auth, single-maintainer —
                see the client module docstring for which endpoints are
                actually alive and the SvelteKit __data.json approach).

Announcements land in the existing `announcements` table with
source="psx_terminal" so they stay distinguishable from the legacy
PSX-portal scraper rows and from any future PUCARS-direct scraper.
Fundamentals are upserted into `company_fundamentals`, one row per
ticker.

KNOWN GAP: PSX Terminal does not list ENGRO (only ENGROH, the
post-merger Engro Holdings entity), so ENGRO is expected to fail here
until the project decides how to handle that corporate action — see
docs/KNOWN_ISSUES.md. No silent ENGRO->ENGROH aliasing is done.
"""

from datetime import datetime, time, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.collectors.announcement_collector import AnnouncementCollector
from app.collectors.base_collector import BaseCollector
from app.db.models import Announcement, CompanyFundamentals
from app.integrations.psx_terminal_client import PSXTerminalClient


class FundamentalsCollector(BaseCollector):
    """
    For each ticker:
    1. Fetch fundamentals -> upsert into company_fundamentals
    2. Fetch the symbol page -> insert new announcements
       (dedup: ticker + title + announcement date)

    A ticker counts as failed only when BOTH fetches come back empty
    (e.g. PSX Terminal doesn't list it at all). One ticker failing never
    aborts the run — same contract as every other collector.
    """

    name = "fundamentals_collector"

    SOURCE = "psx_terminal"
    # Same pacing as announcement_collector — polite to a free
    # single-maintainer service.
    SLEEP_BETWEEN_TICKERS = 3.0
    SLEEP_BETWEEN_REQUESTS = 1.0  # between the 2 fetches for one ticker

    # Title keywords -> category, layered on top of the shared map in
    # AnnouncementCollector.CATEGORY_MAP. These cover phrasings observed
    # live on PSX Terminal that the shared map misses.
    EXTRA_CATEGORY_MAP = {
        "transmission of accounts": "QUARTERLY_RESULT",
        "transmission of annual report": "QUARTERLY_RESULT",
        "disclosure of interest": "MATERIAL_INFO",
    }

    async def collect(self, tickers: list[str]) -> dict:
        processed = 0
        failed = 0
        fundamentals_upserted = 0
        announcements_inserted = 0

        async with PSXTerminalClient() as client:
            # One symbols-list call up front: tickers PSX Terminal doesn't
            # list at all (ENGRO) are flagged without burning 2 requests
            # each. If this call fails, fall through to per-ticker fetches.
            known_symbols = await client.get_symbols()
            if known_symbols is not None:
                known_symbols = set(known_symbols)

            for ticker in tickers:
                try:
                    if (
                        known_symbols is not None
                        and ticker.upper() not in known_symbols
                    ):
                        logger.warning(
                            f"{ticker}: not in PSX Terminal's symbol "
                            f"universe — skipping (no fundamentals, no "
                            f"announcements from this source)"
                        )
                        failed += 1
                        await self.sleep(self.SLEEP_BETWEEN_TICKERS)
                        continue

                    fundamentals = await client.get_fundamentals(ticker)
                    await self.sleep(self.SLEEP_BETWEEN_REQUESTS)
                    symbol_data = await client.get_symbol_data(ticker)

                    if fundamentals is None and symbol_data is None:
                        logger.warning(
                            f"{ticker}: PSX Terminal returned nothing "
                            f"(fundamentals AND symbol page both empty)"
                        )
                        failed += 1
                        await self.sleep(self.SLEEP_BETWEEN_TICKERS)
                        continue

                    if fundamentals is not None:
                        await self._upsert_fundamentals(ticker, fundamentals)
                        fundamentals_upserted += 1

                    if symbol_data is not None:
                        inserted = await self._insert_announcements(
                            ticker, symbol_data["announcements"]
                        )
                        announcements_inserted += inserted
                        logger.info(
                            f"{ticker}: fundamentals "
                            f"{'upserted' if fundamentals else 'MISSING'}, "
                            f"{inserted} new announcements "
                            f"({len(symbol_data['announcements'])} fetched)"
                        )
                    else:
                        logger.warning(
                            f"{ticker}: fundamentals upserted but symbol "
                            f"page (announcements) unavailable"
                        )

                    processed += 1

                except Exception as e:
                    logger.error(
                        f"Fundamentals collection failed for {ticker}: "
                        f"{type(e).__name__}: {e}"
                    )
                    failed += 1

                await self.sleep(self.SLEEP_BETWEEN_TICKERS)

        await self.db.commit()

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": fundamentals_upserted + announcements_inserted,
            "fundamentals_upserted": fundamentals_upserted,
            "announcements_inserted": announcements_inserted,
        }

    async def _upsert_fundamentals(
        self, ticker: str, fundamentals: dict
    ) -> None:
        """
        One row per ticker: update in place if it exists, insert if not.
        Null fields from the source are stored as NULL — never defaulted
        to something plausible-looking.
        """
        existing = await self.db.execute(
            select(CompanyFundamentals).where(
                CompanyFundamentals.ticker == ticker
            )
        )
        row = existing.scalar_one_or_none()

        if row is None:
            row = CompanyFundamentals(ticker=ticker, source=self.SOURCE)
            self.db.add(row)

        row.pe_ratio = fundamentals["pe_ratio"]
        row.dividend_yield = fundamentals["dividend_yield"]
        row.market_cap_pkr = fundamentals["market_cap_pkr"]
        row.free_float_pct = fundamentals["free_float_pct"]
        row.source = self.SOURCE
        row.last_updated = datetime.now(timezone.utc)

    async def _insert_announcements(
        self, ticker: str, announcements: list[dict]
    ) -> int:
        """
        Insert announcements that aren't already stored. Dedup key is
        ticker + title + announcement date (a range match on announced_at,
        so re-runs with a different posting_time don't duplicate).
        """
        inserted = 0

        for item in announcements:
            try:
                title = (item.get("title") or "").strip()
                if not title:
                    continue

                announced_at = self._build_announced_at(
                    item.get("date"), item.get("posting_time")
                )
                if announced_at is None:
                    logger.warning(
                        f"{ticker}: unparseable announcement date "
                        f"{item.get('date')!r} for {title[:60]!r} — skipped"
                    )
                    continue

                day_start = datetime.combine(
                    announced_at.date(), time.min, tzinfo=timezone.utc
                )
                existing = await self.db.execute(
                    select(Announcement.id).where(
                        Announcement.ticker == ticker,
                        Announcement.title == title,
                        Announcement.announced_at >= day_start,
                        Announcement.announced_at
                        < day_start + timedelta(days=1),
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                self.db.add(
                    Announcement(
                        ticker=ticker,
                        announced_at=announced_at,
                        title=title[:500],
                        category=self._map_category(title),
                        pdf_url=item.get("pdf_url"),
                        source=self.SOURCE,
                    )
                )
                inserted += 1

            except Exception as e:
                logger.warning(
                    f"Skipping announcement for {ticker}: "
                    f"{type(e).__name__}: {e}"
                )
                continue

        return inserted

    @staticmethod
    def _build_announced_at(
        raw_date: str | None, raw_time: str | None
    ) -> datetime | None:
        """PSX Terminal serves date as YYYY-MM-DD and time as HH:MM:SS."""
        if not raw_date:
            return None
        try:
            day = datetime.strptime(str(raw_date).strip(), "%Y-%m-%d")
        except ValueError:
            return None
        clock = time.min
        if raw_time:
            try:
                clock = datetime.strptime(
                    str(raw_time).strip(), "%H:%M:%S"
                ).time()
            except ValueError:
                pass  # keep midnight — the date is what matters
        return datetime.combine(day.date(), clock, tzinfo=timezone.utc)

    @classmethod
    def _map_category(cls, title: str) -> str:
        """Shared keyword map first, then the PSX Terminal extras."""
        lower = title.lower()
        for keyword, category in AnnouncementCollector.CATEGORY_MAP.items():
            if keyword in lower:
                return category
        for keyword, category in cls.EXTRA_CATEGORY_MAP.items():
            if keyword in lower:
                return category
        return "OTHER"
