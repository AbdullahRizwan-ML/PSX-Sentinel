"""
PSX Sentinel — Announcement Collector

Collects corporate announcements from the PSX Data Portal Service (DPS).

PRIMARY SOURCE: dps.psx.com.pk
APPROACH: Try JSON API endpoint first, fall back to HTML scraping.
FALLBACK: If PSX portal is unreachable or changes structure,
          log warning and return empty result gracefully.

Announcements include earnings results, dividends, board meetings,
and material information disclosures.
"""

import os
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base_collector import BaseCollector
from app.db.models import Announcement


class AnnouncementCollector(BaseCollector):
    """
    Fetches corporate announcements from the PSX Data Portal.

    Tries two approaches in order:
    1. JSON API at /data/announcements (more reliable, structured)
    2. HTML scraping of /announcements page (fallback)

    Each announcement is classified into a category enum
    (QUARTERLY_RESULT, DIVIDEND, BOARD_MEETING, MATERIAL_INFO, OTHER)
    based on keyword matching in the title.
    """

    name = "announcement_collector"

    BASE_URL = "https://dps.psx.com.pk"
    ANNOUNCEMENTS_URL = "https://dps.psx.com.pk/announcements"
    SLEEP_BETWEEN_TICKERS = 3.0
    PDF_DIR = "data/announcements"

    # Maps lowercase keywords to category enum values
    CATEGORY_MAP = {
        "financial results": "QUARTERLY_RESULT",
        "quarterly results": "QUARTERLY_RESULT",
        "quarterly report": "QUARTERLY_RESULT",
        "annual results": "QUARTERLY_RESULT",
        "half yearly": "QUARTERLY_RESULT",
        "half-yearly": "QUARTERLY_RESULT",
        "condensed interim": "QUARTERLY_RESULT",
        "dividend": "DIVIDEND",
        "bonus shares": "DIVIDEND",
        "right shares": "DIVIDEND",
        "board of directors": "BOARD_MEETING",
        "board meeting": "BOARD_MEETING",
        "directors meeting": "BOARD_MEETING",
        "material information": "MATERIAL_INFO",
        "material event": "MATERIAL_INFO",
        "notice": "MATERIAL_INFO",
    }

    # Standard browser headers to avoid being blocked
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
    }

    async def collect(self, tickers: list[str]) -> dict:
        """
        For each ticker: fetch the last 15 announcements from PSX.

        Steps:
        1. Try JSON API endpoint (/data/announcements?symbol=TICKER)
        2. Fall back to HTML scraping (/announcements?symbol=TICKER)
        3. Parse and classify each announcement
        4. Insert new records (skip duplicates by title match)
        5. Sleep between tickers to respect rate limits

        Returns summary dict.
        """
        os.makedirs(self.PDF_DIR, exist_ok=True)

        processed = 0
        failed = 0
        total_inserted = 0

        async with httpx.AsyncClient(
            headers=self.HEADERS, timeout=30.0, follow_redirects=True
        ) as client:
            for ticker in tickers:
                try:
                    inserted = await self._collect_ticker(client, ticker)
                    total_inserted += inserted
                    processed += 1
                    logger.info(
                        f"{ticker}: {inserted} announcements inserted"
                    )
                except Exception as e:
                    logger.error(
                        f"Announcement collection failed for "
                        f"{ticker}: {type(e).__name__}: {e}"
                    )
                    failed += 1

                await self.sleep(self.SLEEP_BETWEEN_TICKERS)

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": total_inserted,
        }

    async def _collect_ticker(
        self, client: httpx.AsyncClient, ticker: str
    ) -> int:
        """Fetch and store announcements for one ticker."""
        announcements_data: list[dict] = []

        # ── Strategy 1: JSON API endpoint ─────────────────────────────────
        try:
            json_url = (
                f"{self.BASE_URL}/data/announcements"
                f"?symbol={ticker}&start=0&length=15"
            )
            resp = await client.get(json_url)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, dict) and "data" in data:
                        announcements_data = data["data"]
                    elif isinstance(data, list):
                        announcements_data = data
                except Exception:
                    pass  # JSON parse failed — try HTML fallback
        except Exception as e:
            logger.debug(
                f"JSON endpoint failed for {ticker}: {e}"
            )

        # ── Strategy 2: HTML scraping fallback ────────────────────────────
        if not announcements_data:
            try:
                html_url = f"{self.ANNOUNCEMENTS_URL}?symbol={ticker}"
                resp = await client.get(html_url)
                if resp.status_code == 200:
                    announcements_data = self._parse_html_announcements(
                        resp.text, ticker
                    )
            except Exception as e:
                logger.warning(
                    f"HTML scraping failed for {ticker}: {e}"
                )

        if not announcements_data:
            logger.warning(
                f"No announcements found for {ticker} from any source"
            )
            return 0

        return await self._insert_announcements(ticker, announcements_data)

    def _parse_html_announcements(
        self, html: str, ticker: str
    ) -> list[dict]:
        """
        Parse PSX announcement HTML page.

        PSX uses various table structures — this tries multiple
        selectors to find announcement rows. Returns a list of
        dicts with date, title, category, and pdf_url fields.
        """
        results: list[dict] = []
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Try multiple CSS selectors — PSX changes layout occasionally
            rows = (
                soup.find_all("tr", class_="announcement-row")
                or soup.select("table tbody tr")
                or soup.find_all("tr")[1:]  # Skip header row
            )

            for row in rows[:15]:  # Cap at 15 announcements
                cells = row.find_all("td")
                if len(cells) >= 3:
                    # Extract PDF link from the last cell if present
                    pdf_link = cells[-1].find("a")
                    pdf_url = (
                        pdf_link["href"]
                        if pdf_link and pdf_link.get("href")
                        else None
                    )

                    results.append(
                        {
                            "date": cells[0].get_text(strip=True),
                            "title": (
                                cells[1].get_text(strip=True)
                                or cells[2].get_text(strip=True)
                            ),
                            "category": "",
                            "pdf_url": pdf_url,
                        }
                    )
        except Exception as e:
            logger.warning(f"HTML parse error for {ticker}: {e}")

        return results

    async def _insert_announcements(
        self, ticker: str, data: list[dict]
    ) -> int:
        """
        Insert announcement records into the database.

        Skips duplicates by checking if an announcement with the
        same ticker + title already exists. This is simpler and
        more reliable than matching on date (which can have format issues).
        """
        inserted = 0

        for item in data:
            try:
                # Extract title from various possible keys
                title = (
                    item.get("title", "")
                    or item.get("Title", "")
                    or item.get("subject", "")
                    or item.get("Subject", "")
                ).strip()

                if not title:
                    continue

                # Parse the announcement date
                raw_date = (
                    item.get("date", "")
                    or item.get("Date", "")
                    or ""
                ).strip()
                announced_at = self._parse_date(raw_date)
                if announced_at is None:
                    announced_at = datetime.utcnow()

                # Map category from title/category text
                category = self._map_category(
                    item.get("category", "")
                    or item.get("Category", "")
                    or title
                )

                # Build full PDF URL if relative path
                pdf_url = (
                    item.get("pdf_url")
                    or item.get("attachment")
                    or item.get("Attachment")
                )
                if pdf_url and not pdf_url.startswith("http"):
                    pdf_url = f"{self.BASE_URL}{pdf_url}"

                # Check for duplicate (same ticker + title)
                existing = await self.db.execute(
                    select(Announcement).where(
                        Announcement.ticker == ticker,
                        Announcement.title == title,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                announcement = Announcement(
                    ticker=ticker,
                    announced_at=announced_at,
                    title=title,
                    category=category,
                    pdf_url=pdf_url,
                    source="psx_dps",
                )
                self.db.add(announcement)
                inserted += 1

            except Exception as e:
                logger.warning(
                    f"Skipping announcement for {ticker}: {e}"
                )
                continue

        await self.db.commit()
        return inserted

    def _parse_date(self, raw: str) -> datetime | None:
        """
        Try multiple date formats commonly used by PSX.

        PSX portal uses inconsistent date formats across different
        pages and API versions, so we try all known patterns.
        """
        formats = [
            "%d-%b-%Y",   # 15-Jan-2024
            "%d/%m/%Y",   # 15/01/2024
            "%Y-%m-%d",   # 2024-01-15
            "%d %b %Y",   # 15 Jan 2024
            "%B %d, %Y",  # January 15, 2024
            "%d-%m-%Y",   # 15-01-2024
            "%b %d, %Y",  # Jan 15, 2024
        ]
        for fmt in formats:
            try:
                return datetime.strptime(raw.strip(), fmt)
            except (ValueError, AttributeError):
                continue
        return None

    def _map_category(self, text: str) -> str:
        """
        Map raw category/title text to the announcement category enum.

        Scans for keywords in the input text (case-insensitive) and
        returns the first matching category. Falls back to "OTHER".
        """
        lower = text.lower()
        for keyword, category in self.CATEGORY_MAP.items():
            if keyword in lower:
                return category
        return "OTHER"
