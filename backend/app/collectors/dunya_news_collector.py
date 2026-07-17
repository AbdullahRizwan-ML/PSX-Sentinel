"""
PSX Sentinel — Dunya News Collector

Scrapes Dunya News's English business section (dunyanews.tv/en/Business),
which — unlike Dunya's RSS feed (Cloudflare-blocked, see KNOWN_ISSUES) —
is plain static server-rendered HTML (re-verified live 2026-07-17,
HTTP 200 via httpx, no challenge). No headless browser needed.

Same shape and matching rules as news_collector.py (ARY RSS): headline
keyword matching against the active ticker universe, one NewsArticle row
per matched ticker, URL-uniqueness dedup. Articles land with
source="dunya" so they never conflate with ARY rows.

The listing page carries headlines + links only (no dates/summaries), so
the collector fetches the article page for MATCHED articles only (matches
are rare, so this stays light): summary from <meta name="description">,
published_at from the <time datetime="YYYY-MM-DD"> tag (labelled
"Updated on" on the site — treated as the publish date, documented
approximation).
"""

import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select

from app.collectors.base_collector import BaseCollector
from app.db.models import Company, NewsArticle


class DunyaNewsCollector(BaseCollector):
    """
    Fetches the Dunya Business listing page, matches headlines to
    monitored tickers (ticker symbol or first word of company name,
    case-insensitive — identical rule to NewsCollector), then enriches
    matched articles from their article pages.

    Politeness: one listing fetch per run + one page fetch per *matched*
    article, 1s sleep between article fetches.
    """

    name = "dunya_news_collector"

    SOURCE = "dunya"
    LISTING_URL = "https://dunyanews.tv/en/Business"
    BASE_URL = "https://dunyanews.tv"
    SLEEP_BETWEEN_ARTICLES = 1.0

    # Article links look like /en/Business/963037-slug or
    # /index.php/en/Business/963037-slug (both seen live; the index.php
    # variant 301s to the canonical /en/ form).
    ARTICLE_HREF_RE = re.compile(r"^(?:/index\.php)?(/en/Business/\d+-[^/?#]+)$")

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
        1. Build ticker -> company_name lookup from the Company table
        2. Fetch + parse the Business listing page (static HTML)
        3. Match headlines against tickers
        4. For new matched articles: fetch the article page for
           summary/date, insert one NewsArticle row per matched ticker
        """
        result = await self.db.execute(select(Company))
        companies = result.scalars().all()
        ticker_lookup = {
            c.ticker: c.name for c in companies if c.ticker in tickers
        }

        if not ticker_lookup:
            logger.warning("No companies found in DB. Run seed_companies() first.")
            return {
                "tickers_processed": 0,
                "tickers_failed": 0,
                "records_inserted": 0,
            }

        inserted = 0
        try:
            async with httpx.AsyncClient(
                headers=self.HEADERS, timeout=20.0, follow_redirects=True
            ) as client:
                articles = await self._fetch_listing(client)
                if not articles:
                    logger.warning("Dunya Business listing returned 0 articles")
                    return {
                        "tickers_processed": 0,
                        "tickers_failed": 1,
                        "records_inserted": 0,
                    }

                inserted = await self._process_articles(
                    client, articles, ticker_lookup
                )
                logger.info(
                    f"dunya: {len(articles)} headlines fetched, "
                    f"{inserted} articles inserted"
                )
        except Exception as e:
            logger.error(f"Dunya collection failed: {type(e).__name__}: {e}")
            return {
                "tickers_processed": 0,
                "tickers_failed": 1,
                "records_inserted": inserted,
            }

        return {
            "tickers_processed": 1,  # one source processed
            "tickers_failed": 0,
            "records_inserted": inserted,
        }

    async def _fetch_listing(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch the Business listing and return [{url, headline}, ...]."""
        resp = await client.get(self.LISTING_URL)
        if resp.status_code != 200:
            logger.warning(f"Dunya listing HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        seen: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            m = self.ARTICLE_HREF_RE.match(a["href"].strip())
            if not m:
                continue
            headline = a.get_text(strip=True)
            # Listing repeats links (image + title anchors); keep the
            # longest text per URL and skip anchors with no real title.
            canonical = f"{self.BASE_URL}{m.group(1)}"
            if len(headline) > len(seen.get(canonical, "")):
                seen[canonical] = headline

        articles = [
            {"url": url, "headline": headline}
            for url, headline in seen.items()
            if len(headline) >= 25
        ]
        logger.info(f"Dunya listing: {len(articles)} unique articles")
        return articles

    async def _process_articles(
        self,
        client: httpx.AsyncClient,
        articles: list[dict],
        ticker_lookup: dict[str, str],
    ) -> int:
        """Match, enrich (matched only), insert. Returns rows inserted."""
        inserted = 0

        for item in articles:
            try:
                url = item["url"]
                headline = item["headline"]

                search_text = headline.upper()
                matched = [
                    ticker
                    for ticker, name in ticker_lookup.items()
                    if ticker.upper() in search_text
                    or name.split()[0].upper() in search_text
                ]
                if not matched:
                    continue

                existing = await self.db.execute(
                    select(NewsArticle).where(NewsArticle.url == url)
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                summary, published_at = await self._fetch_article_details(
                    client, url
                )
                await self.sleep(self.SLEEP_BETWEEN_ARTICLES)

                for ticker in matched:
                    article_url = (
                        url if ticker == matched[0]
                        else f"{url}#ticker={ticker}"
                    )
                    self.db.add(
                        NewsArticle(
                            ticker=ticker,
                            source=self.SOURCE,
                            headline=headline[:500],
                            summary=summary[:2000] if summary else None,
                            url=article_url,
                            published_at=published_at,
                            word_count=(
                                len(summary.split()) if summary else 0
                            ),
                        )
                    )
                    inserted += 1
                logger.info(
                    f"Dunya match: {', '.join(matched)} <- {headline[:70]!r}"
                )

            except Exception as e:
                logger.warning(
                    f"Skipping Dunya article: {type(e).__name__}: {e}"
                )
                continue

        await self.db.commit()
        return inserted

    async def _fetch_article_details(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[str | None, datetime]:
        """
        Fetch one article page; return (summary, published_at).
        Both degrade gracefully: summary None, published_at now-UTC.
        """
        summary: str | None = None
        published_at = datetime.now(timezone.utc)
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Dunya article HTTP {resp.status_code}: {url}")
                return summary, published_at

            soup = BeautifulSoup(resp.text, "html.parser")

            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                summary = meta["content"].strip() or None

            time_tag = soup.find("time", attrs={"datetime": True})
            if time_tag:
                try:
                    published_at = datetime.strptime(
                        time_tag["datetime"].strip()[:10], "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass  # keep the now-UTC fallback
        except Exception as e:
            logger.warning(
                f"Dunya article fetch failed ({url}): {type(e).__name__}: {e}"
            )
        return summary, published_at
