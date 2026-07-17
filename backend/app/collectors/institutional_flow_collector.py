"""
PSX Sentinel — Institutional Flow Collector (NCCPL FIPI/LIPI)

Drives a headless browser (Playwright) through the NCCPL flow mapped in
Phase 5 Session 3:
  1. load /market-information so Cloudflare issues a clearance cookie and
     Laravel sets the CSRF meta tag + XSRF cookie,
  2. read the CSRF token from <meta name="csrf-token">,
  3. from inside the page context, POST to the internal JSON API
     /api/{fipi,lipi}-{normal,sector-wise}/data — one request per date
     (ranged sector responses carry no date column, so day-by-day is the
     only way to attribute a date), for each date in the backfill window,
  4. upsert into institutional_flows (dedup via ON CONFLICT DO NOTHING on
     uq_flow_row; TOTAL rollup + blank/separator rows are dropped).

Granularity is sector-level at best (no per-ticker data); archive depth
is 2015-12-09 onward. See docs/KNOWN_ISSUES.md for the full data-shape
notes and the categories.

⚠️ ENVIRONMENT CAVEAT (verified Phase 5 Session 4, 2026-07-17):
NCCPL's Cloudflare protection is an *interactive* challenge. On the dev
machine this collector was written on, automated Playwright could NOT
pass it — neither the bundled Chromium (which additionally fails to
launch here with a Windows side-by-side error) nor headed/headless
system Chrome with automation-fingerprint stealth flags. Only a genuine
(non-automated) browser session cleared it. So this collector is
CORRECT and reusable, but on an automation-flagged IP/browser it will
return 0 rows with a logged warning rather than data. The Session 4
backfill that actually populated institutional_flows was fetched through
a real browser session and bulk-loaded; see the Build Log. A production
deployment needs either a residential/clean IP, a CAPTCHA-solving
service, or a periodic manual export. Playwright's chromium binary must
also be installed at deploy time (Railway) — `playwright install
chromium`.

Never raises out of collect(): every failure path logs and returns a
zeroed/partial summary, per the BaseCollector contract.
"""

import asyncio
from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import text

from app.collectors.base_collector import BaseCollector

MARKET_INFO_URL = "https://www.nccpl.com.pk/market-information"

# dataset key -> (api path, request-body builder taking an iso date)
DATASETS = {
    "fipi_normal": ("/api/fipi-normal/data", "date"),
    "lipi_normal": ("/api/lipi-normal/data", "date"),
    "fipi_sector_wise": ("/api/fipi-sector-wise/data", "range"),
    "lipi_sector_wise": ("/api/lipi-sector-wise/data", "range"),
}


class InstitutionalFlowCollector(BaseCollector):
    """
    Populates institutional_flows from NCCPL FIPI/LIPI.

    Note: does not use the `tickers` argument — NCCPL data is market/
    sector level, not per-ticker. `collect()` accepts it for interface
    compatibility with BaseCollector and ignores it.

    backfill_days controls how far back the window reaches (default 30
    calendar days). Non-trading days simply return no rows.
    """

    name = "institutional_flow_collector"

    SOURCE = "nccpl"
    CHALLENGE_WAIT_SECONDS = 40
    SLEEP_BETWEEN_REQUESTS = 0.5

    def __init__(self, db, backfill_days: int = 30) -> None:
        super().__init__(db)
        self.backfill_days = backfill_days

    async def collect(self, tickers: list[str]) -> dict:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error(
                "playwright not installed — cannot collect NCCPL flows. "
                "pip install playwright && playwright install chromium"
            )
            return self._summary(0, 1, 0)

        end = date.today()
        dates = [end - timedelta(days=i) for i in range(self.backfill_days)]
        dates.sort()
        iso_dates = [d.isoformat() for d in dates]

        records: list[dict] = []
        try:
            async with async_playwright() as p:
                browser = await self._launch(p)
                if browser is None:
                    return self._summary(0, 1, 0)
                try:
                    ctx = await browser.new_context(locale="en-US")
                    page = await ctx.new_page()
                    csrf = await self._pass_challenge(page)
                    if not csrf:
                        logger.error(
                            "NCCPL Cloudflare challenge not passed — "
                            "automation is blocked on this IP/browser. "
                            "See collector docstring + KNOWN_ISSUES."
                        )
                        return self._summary(0, 1, 0)

                    for iso in iso_dates:
                        for ds_key, (path, mode) in DATASETS.items():
                            rows = await self._fetch(page, csrf, path, mode, iso)
                            records.extend(self._normalise(ds_key, iso, rows))
                            await asyncio.sleep(self.SLEEP_BETWEEN_REQUESTS)
                finally:
                    await browser.close()
        except Exception as e:
            logger.error(
                f"NCCPL collection failed: {type(e).__name__}: {e}"
            )
            return self._summary(0, 1, len(records))

        inserted = await self._upsert(records)
        logger.info(
            f"NCCPL: {len(records)} rows parsed across "
            f"{len(iso_dates)} days, {inserted} newly inserted"
        )
        return self._summary(len(iso_dates), 0, inserted)

    # ── browser plumbing ──────────────────────────────────────────────

    async def _launch(self, p):
        """Prefer bundled chromium; fall back to system Chrome channel."""
        for kwargs in ({}, {"channel": "chrome"}):
            try:
                return await p.chromium.launch(headless=True, **kwargs)
            except Exception as e:
                logger.warning(
                    f"chromium launch {kwargs or 'default'} failed: "
                    f"{type(e).__name__}: {e}"
                )
        return None

    async def _pass_challenge(self, page) -> str | None:
        """Load the page, wait out the challenge, return CSRF or None."""
        await page.goto(
            MARKET_INFO_URL, wait_until="domcontentloaded", timeout=60000
        )
        waited = 0
        while waited < self.CHALLENGE_WAIT_SECONDS:
            try:
                title = (await page.title()).lower()
            except Exception:
                title = "..."
            if "moment" not in title and "attention" not in title:
                csrf = await page.evaluate(
                    "document.querySelector('meta[name=\"csrf-token\"]')"
                    "?.getAttribute('content')"
                )
                if csrf:
                    return csrf
            await asyncio.sleep(4)
            waited += 4
        return None

    async def _fetch(self, page, csrf, path, mode, iso) -> list:
        """Run the in-page POST for one dataset+date. [] on any failure."""
        body = (
            {"date": iso} if mode == "date"
            else {"fromDate": iso, "toDate": iso}
        )
        try:
            result = await page.evaluate(
                """async ({path, csrf, body}) => {
                    const r = await fetch(path, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json',
                                  'X-CSRF-TOKEN': csrf},
                        body: JSON.stringify(body),
                    });
                    if (!r.ok) return {ok: false, status: r.status};
                    const j = await r.json();
                    return {ok: true, rows: j.records || j.data || []};
                }""",
                {"path": path, "csrf": csrf, "body": body},
            )
            if not result.get("ok"):
                logger.warning(
                    f"NCCPL {path} {iso} HTTP {result.get('status')}"
                )
                return []
            return result.get("rows") or []
        except Exception as e:
            logger.warning(
                f"NCCPL {path} {iso} failed: {type(e).__name__}: {e}"
            )
            return []

    # ── row shaping + persistence ─────────────────────────────────────

    @staticmethod
    def _to_int(v):
        if v in (None, "", " "):
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(v):
        if v in (None, "", " "):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _normalise(self, dataset: str, iso: str, rows: list) -> list[dict]:
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            market_type = (r.get("MARKET_TYPE") or "").strip()
            if market_type.upper() == "TOTAL":
                continue  # derived rollup — not stored
            client_type = (r.get("CLIENT_TYPE") or "").strip()
            if not client_type:
                continue
            sector_name = (r.get("SECTOR_NAME") or "").strip() or None
            if sector_name == "---":
                continue  # separator row
            out.append({
                "date": iso,
                "dataset": dataset,
                "client_type": client_type,
                "sector_code": (r.get("SEC_CODE") or "").strip() or None,
                "sector_name": sector_name,
                "market_type": market_type,
                "buy_volume": self._to_int(r.get("BUY_VOLUME")),
                "buy_value": self._to_float(r.get("BUY_VALUE")),
                "sell_volume": self._to_int(r.get("SELL_VOLUME")),
                "sell_value": self._to_float(r.get("SELL_VALUE")),
                "net_volume": self._to_int(r.get("NET_VOLUME")),
                "net_value": self._to_float(r.get("NET_VALUE")),
                "usd_value": self._to_float(r.get("USD")),
            })
        return out

    async def _upsert(self, records: list[dict]) -> int:
        if not records:
            return 0
        ins = text(
            "INSERT INTO institutional_flows "
            "(id, date, dataset, client_type, sector_code, sector_name, "
            " market_type, buy_volume, buy_value, sell_volume, sell_value, "
            " net_volume, net_value, usd_value, source) VALUES "
            "(gen_random_uuid(), :date, :dataset, :client_type, "
            " :sector_code, :sector_name, :market_type, :buy_volume, "
            " :buy_value, :sell_volume, :sell_value, :net_volume, "
            " :net_value, :usd_value, :source) "
            "ON CONFLICT ON CONSTRAINT uq_flow_row DO NOTHING"
        )
        before = (
            await self.db.execute(
                text("SELECT COUNT(*) FROM institutional_flows")
            )
        ).scalar()
        for r in records:
            await self.db.execute(ins, {**r, "source": self.SOURCE})
        await self.db.commit()
        after = (
            await self.db.execute(
                text("SELECT COUNT(*) FROM institutional_flows")
            )
        ).scalar()
        return after - before

    def _summary(self, processed, failed, inserted) -> dict:
        return {
            "tickers_processed": processed,  # days processed (n/a per-ticker)
            "tickers_failed": failed,
            "records_inserted": inserted,
        }
