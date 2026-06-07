"""
PSX Sentinel — News Collector

Collects financial news articles from Pakistani RSS feeds and matches
them to monitored PSX tickers using keyword matching.

ACTIVE SOURCES:
1. ARY News Business: https://arynews.tv/category/business/feed/

SOURCES REMOVED (Cloudflare blocked — HTTP 403):
- Dawn Business:        https://www.dawn.com/business/rss
- Business Recorder:    https://www.brecorder.com/rss/home
- Profit Pakistan Today: https://profit.pakistantoday.com.pk/feed/

FUTURE SOURCES (Phase 2B, requires Playwright):
- Sarmaaya.pk: https://sarmaaya.pk (PSX-authorized data vendor)

feedparser is used for XML parsing after httpx fetches the raw feed.
"""

import html as html_lib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base_collector import BaseCollector
from app.db.models import Company, NewsArticle


class NewsCollector(BaseCollector):
    """
    Fetches Pakistani financial news from RSS feeds and links articles
    to monitored tickers via headline/summary keyword matching.

    Matching rules:
    - Match the ticker string (e.g. "ENGRO") in headline or summary
    - Match the first word of the company name (e.g. "Engro")
    - Case-insensitive search
    - One article can be linked to multiple tickers (one DB row per match)
    - Articles with URLs that already exist in DB are skipped
    """

    name = "news_collector"

    RSS_FEEDS = {
        "arynews": "https://arynews.tv/category/business/feed/",
    }

    # SOURCES REMOVED (Cloudflare blocked):
    # Dawn Business: https://www.dawn.com/business/rss  -> 403
    # Business Recorder: https://www.brecorder.com/rss/home -> 403
    # Profit Today: https://profit.pakistantoday.com.pk/feed/ -> 403
    #
    # FUTURE SOURCES to add when Playwright is available (Phase 2B):
    # Sarmaaya.pk: https://sarmaaya.pk (PSX-authorized data vendor)

    async def collect(self, tickers: list[str]) -> dict:
        """
        Fetch all RSS feeds and match articles to tickers.

        Steps:
        1. Build ticker -> company_name lookup from the Company table
        2. For each RSS feed:
           a. Parse feed entries via feedparser (in thread pool)
           b. For each entry: match headline + summary against tickers
           c. Insert one NewsArticle row per ticker match
        3. Return summary

        Returns dict with tickers_processed (feeds processed),
        tickers_failed (feeds that errored), records_inserted.
        """
        # Build ticker -> company name lookup from DB
        result = await self.db.execute(select(Company))
        companies = result.scalars().all()
        ticker_lookup = {
            c.ticker: c.name
            for c in companies
            if c.ticker in tickers
        }

        if not ticker_lookup:
            logger.warning(
                "No companies found in DB. "
                "Run seed_companies() first."
            )
            return {
                "tickers_processed": 0,
                "tickers_failed": 0,
                "records_inserted": 0,
            }

        processed = 0
        failed = 0
        total_inserted = 0

        for source, feed_url in self.RSS_FEEDS.items():
            try:
                logger.info(f"Fetching RSS feed: {source} ({feed_url})")

                entries = await self._fetch_feed_safe(source, feed_url)
                if not entries:
                    failed += 1
                    continue

                inserted = await self._process_feed_entries(
                    entries, source, ticker_lookup
                )
                total_inserted += inserted
                processed += 1
                logger.info(
                    f"{source}: {len(entries)} entries fetched, "
                    f"{inserted} articles inserted"
                )

                await self.sleep(1.0)

            except Exception as e:
                logger.error(
                    f"RSS feed {source} failed: "
                    f"{type(e).__name__}: {e}"
                )
                failed += 1

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": total_inserted,
        }

    async def _fetch_feed_safe(self, name: str, url: str) -> list:
        """
        Fetch RSS via httpx with proper headers, clean XML,
        then parse with feedparser. Returns entries list.

        This two-step approach (httpx fetch + feedparser parse) avoids
        Cloudflare blocks that occur when feedparser fetches directly.
        Also cleans invalid XML characters that break feedparser.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "application/rss+xml, application/xml, "
                "text/xml, */*"
            ),
        }
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        f"RSS {name} HTTP {resp.status_code}"
                    )
                    return []

                text = resp.text
                # Remove invalid XML control characters
                text = re.sub(
                    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text
                )
                # Fix unescaped ampersands outside valid XML entities
                text = re.sub(
                    r"&(?!(amp|lt|gt|quot|apos|#\d+|"
                    r"#x[0-9a-fA-F]+);)",
                    "&amp;",
                    text,
                )

                feed = feedparser.parse(text)
                entries = getattr(feed, "entries", [])
                logger.info(
                    f"RSS {name}: {len(entries)} entries "
                    f"(bozo={feed.bozo})"
                )
                return entries

        except Exception as e:
            logger.warning(f"RSS {name} fetch error: {e}")
            return []

    async def _process_feed_entries(
        self,
        entries: list,
        source: str,
        ticker_lookup: dict[str, str],
    ) -> int:
        """
        Match feed entries to tickers and insert NewsArticle records.

        For each entry:
        1. Extract and clean headline and summary
        2. Parse published date (RFC 2822 format from RSS)
        3. Check URL uniqueness to avoid duplicate articles
        4. Match tickers via headline + summary keyword search
        5. Insert one row per matched ticker

        Returns count of records inserted.
        """
        inserted = 0

        for entry in entries:
            try:
                url = entry.get("link", "").strip()
                if not url:
                    continue

                # Clean headline — unescape HTML entities
                headline = html_lib.unescape(
                    entry.get("title", "")
                ).strip()

                # Clean summary — unescape and strip HTML tags
                raw_summary = html_lib.unescape(
                    entry.get("summary", "")
                    or entry.get("description", "")
                ).strip()
                summary = re.sub(r"<[^>]+>", "", raw_summary).strip()

                # Parse published date from RSS
                published_str = (
                    entry.get("published")
                    or entry.get("updated")
                )
                published_at = self._parse_published_date(published_str)

                # Check if this URL already exists in the database
                existing = await self.db.execute(
                    select(NewsArticle).where(NewsArticle.url == url)
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                # Match article against monitored tickers
                search_text = f"{headline} {summary}".upper()
                matched_tickers = []

                for ticker, company_name in ticker_lookup.items():
                    # Match ticker symbol (e.g. "ENGRO")
                    # or first word of company name (e.g. "Engro")
                    first_word = company_name.split()[0].upper()
                    if (
                        ticker.upper() in search_text
                        or first_word in search_text
                    ):
                        matched_tickers.append(ticker)

                if not matched_tickers:
                    continue

                # Insert one record per matched ticker
                for ticker in matched_tickers:
                    # For the second+ ticker, we need a unique URL.
                    # Append ticker as a fragment to avoid the UNIQUE
                    # constraint on url while preserving the original link.
                    article_url = (
                        url if ticker == matched_tickers[0]
                        else f"{url}#ticker={ticker}"
                    )

                    article = NewsArticle(
                        ticker=ticker,
                        source=source,
                        headline=headline[:500],
                        summary=summary[:2000] if summary else None,
                        url=article_url,
                        published_at=published_at,
                        word_count=len(summary.split()) if summary else 0,
                    )
                    self.db.add(article)
                    inserted += 1

            except Exception as e:
                logger.warning(
                    f"Skipping entry from {source}: "
                    f"{type(e).__name__}: {e}"
                )
                continue

        await self.db.commit()
        return inserted

    def _parse_published_date(self, date_str: str | None) -> datetime:
        """
        Parse RSS published date (typically RFC 2822 format).

        Falls back to current UTC time if parsing fails.
        Strips timezone info for DB compatibility (our DateTime
        columns handle timezone-aware datetimes, but some feeds
        return naive datetimes).
        """
        if not date_str:
            return datetime.utcnow()

        try:
            dt = parsedate_to_datetime(date_str)
            return dt
        except Exception:
            pass

        # Fallback: try common date formats
        formats = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, AttributeError):
                continue

        return datetime.utcnow()
