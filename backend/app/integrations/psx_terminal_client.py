"""
PSX Sentinel — PSX Terminal Client

Async client for psxterminal.com, a free PSX market-data site run by a
single maintainer (Runtime Technologies). Used as a fundamentals source
and as a mirror for PSX corporate announcements (PUCARS itself is
login-walled — see docs/KNOWN_ISSUES.md).

IMPORTANT — the documented REST API is mostly dead (verified 2026-07-17):
    /api/symbols                     -> works, no auth
    /api/status                      -> works
    /api/fundamentals/{SYMBOL}       -> server kills the connection
    /api/companies/{SYMBOL}          -> server kills the connection
    /api/dividends/{SYMBOL}          -> server kills the connection
    /api/announcements/{SYMBOL}      -> server kills the connection
The dead endpoints fail even from the site's own origin in a real
browser — the site itself no longer uses them (its GitHub repo,
mumtazkahn/psx-terminal, is gone too). The site is a SvelteKit SSR app,
so the working data channel is the __data.json payload each page is
rendered from:

    /financials/{SYMBOL}/__data.json   -> overview + TTM ratios
                                          (P/E, dividend yield, market
                                          cap, shares/free float)
    /symbol/{SYMBOL}/__data.json?market=REG
                                       -> company profile, dividend
                                          history, latest 10 per-symbol
                                          announcements (with pdf links
                                          to dps.psx.com.pk documents)

Those payloads are in SvelteKit's "devalue" serialisation (a flat array
where objects/arrays hold integer indices into the same array) —
_devalue_parse() below resolves it. `?x-sveltekit-invalidated=01` asks
the server to skip the heavy shared layout node (~350KB of market-wide
data we don't need), which is both faster and politer.

Being an undocumented internal format, this can break whenever the site
is redeployed. Every parse failure degrades to None + a logged warning,
never an exception upward.
"""

from typing import Any, Optional

import httpx
from loguru import logger


def _devalue_parse(values: list) -> Any:
    """
    Resolve a SvelteKit devalue flat array into plain Python objects.

    In devalue, element 0 is the root. Dict values / list elements are
    integer indices into the same flat array. A few negative indices are
    sentinels (-1 undefined, -3 NaN, ...), and special typed values are
    encoded as ["Date", iso_string]-style arrays.
    """
    cache: dict[int, Any] = {}

    def resolve(index: int) -> Any:
        if index in (-1, -2):  # undefined / hole
            return None
        if index == -3:
            return float("nan")
        if index == -4:
            return float("inf")
        if index == -5:
            return float("-inf")
        if index == -6:
            return -0.0
        if index in cache:
            return cache[index]

        v = values[index]

        if isinstance(v, list):
            # Typed encodings: ["Date", "..."], ["Set", ...], ["Map", ...]
            if v and isinstance(v[0], str) and v[0] in (
                "Date", "Set", "Map", "RegExp", "Object", "BigInt",
                "null-prototype",
            ):
                if v[0] == "Date":
                    result: Any = v[1]
                elif v[0] == "Set":
                    result = [resolve(i) for i in v[1:]]
                elif v[0] == "Map":
                    result = {
                        resolve(v[i]): resolve(v[i + 1])
                        for i in range(1, len(v), 2)
                    }
                elif v[0] == "BigInt":
                    result = int(v[1])
                else:
                    result = v[1] if len(v) > 1 else None
                cache[index] = result
                return result

            result = []
            cache[index] = result  # pre-cache so cycles can't recurse forever
            for i in v:
                result.append(resolve(i))
            return result

        if isinstance(v, dict):
            result = {}
            cache[index] = result
            for k, i in v.items():
                result[k] = resolve(i)
            return result

        cache[index] = v
        return v

    return resolve(0)


class PSXTerminalClient:
    """
    Polite async client for psxterminal.com.

    Usage:
        async with PSXTerminalClient() as client:
            symbols = await client.get_symbols()
            fundamentals = await client.get_fundamentals("PPL")
            symbol_data = await client.get_symbol_data("PPL")

    One method per endpoint used. Every method returns None on failure
    (network error, symbol not found, unparseable payload) and logs why —
    it never raises. This is a single-maintainer free service: keep the
    request count low (2 requests per ticker per run) and let the
    collector sleep between tickers.
    """

    BASE_URL = "https://psxterminal.com"
    TIMEOUT = 30.0

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "PSXTerminalClient":
        self._client = httpx.AsyncClient(
            headers=self.HEADERS, timeout=self.TIMEOUT, follow_redirects=True
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_json(self, path: str) -> Any:
        """GET a JSON document. Returns parsed JSON or None (logged)."""
        assert self._client is not None, "use 'async with PSXTerminalClient()'"
        url = f"{self.BASE_URL}{path}"
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    f"PSX Terminal {resp.status_code} for {path}: "
                    f"{resp.text[:200]}"
                )
                return None
            return resp.json()
        except Exception as e:
            logger.warning(
                f"PSX Terminal fetch failed for {path}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    async def _get_page_node(self, path: str) -> Optional[dict]:
        """
        Fetch a SvelteKit __data.json payload and return the resolved
        leaf (page) node as a dict. None if anything about the shape is
        unexpected — logged, never raised.
        """
        payload = await self._get_json(path)
        if not isinstance(payload, dict) or not payload.get("nodes"):
            return None
        try:
            # The leaf page node is the last node of type "data"
            # (node 0 is the shared layout, skipped via
            # x-sveltekit-invalidated=01).
            for node in reversed(payload["nodes"]):
                if isinstance(node, dict) and node.get("type") == "data":
                    resolved = _devalue_parse(node["data"])
                    return resolved if isinstance(resolved, dict) else None
            logger.warning(f"PSX Terminal: no data node in {path}")
            return None
        except Exception as e:
            logger.warning(
                f"PSX Terminal: devalue parse failed for {path}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    # ── Endpoint 1: /api/symbols ──────────────────────────────────────────

    async def get_symbols(self) -> Optional[list[str]]:
        """
        Full listed-symbol universe. The one documented REST endpoint
        that still works. Returns None on failure.

        NOTE: symbols are plain PSX codes ("PPL", "MCB"), no .KA suffix.
        ENGRO is NOT in this universe — PSX Terminal only lists ENGROH
        (Engro Holdings, the post-merger entity).
        """
        payload = await self._get_json("/api/symbols")
        if (
            isinstance(payload, dict)
            and payload.get("success")
            and isinstance(payload.get("data"), list)
        ):
            return payload["data"]
        logger.warning("PSX Terminal /api/symbols returned unexpected shape")
        return None

    # ── Endpoint 2: /financials/{symbol}/__data.json ─────────────────────

    async def get_fundamentals(self, symbol: str) -> Optional[dict]:
        """
        Fundamentals for one symbol, from the financial-overview page
        payload. Returns:

            {
                "symbol": str,
                "pe_ratio": float | None,        # TTM price/earnings
                "dividend_yield": float | None,  # percent, e.g. 3.79
                "market_cap_pkr": float | None,  # full PKR
                "free_float_pct": float | None,  # percent, e.g. 25.14
                "missing_fields": [str, ...],    # what came back null
            }

        or None when the symbol is unknown to PSX Terminal or the fetch/
        parse failed. Missing individual fields are logged per ticker and
        listed in "missing_fields" — never silently defaulted.
        """
        symbol = symbol.upper()
        node = await self._get_page_node(
            f"/financials/{symbol}/__data.json?x-sveltekit-invalidated=01"
        )
        if node is None:
            return None
        if node.get("error") or not isinstance(node.get("overview"), dict):
            logger.warning(
                f"PSX Terminal has no financials for {symbol}: "
                f"{node.get('error', 'no overview section')}"
            )
            return None

        overview = node["overview"]
        ttm = node.get("ttm") or {}

        def section(name: str) -> dict:
            sec = overview.get(name)
            return sec if isinstance(sec, dict) else {}

        pe_ratio = _as_float(ttm.get("price_earnings"))
        dividend_yield = _as_float(section("dividends").get("dividends_yield"))
        market_cap_pkr = _as_float(section("valuation").get("market_cap_basic"))

        shares = section("shares")
        total = _as_float(shares.get("total_shares_outstanding"))
        floated = _as_float(shares.get("float_shares_outstanding"))
        free_float_pct = (
            round(floated / total * 100.0, 4) if total and floated else None
        )

        result = {
            "symbol": symbol,
            "pe_ratio": pe_ratio,
            "dividend_yield": dividend_yield,
            "market_cap_pkr": market_cap_pkr,
            "free_float_pct": free_float_pct,
        }
        missing = [k for k, v in result.items() if k != "symbol" and v is None]
        result["missing_fields"] = missing
        if missing:
            logger.warning(
                f"PSX Terminal fundamentals for {symbol} missing: "
                f"{', '.join(missing)}"
            )
        return result

    # ── Endpoint 3: /symbol/{symbol}/__data.json ──────────────────────────

    async def get_symbol_data(self, symbol: str) -> Optional[dict]:
        """
        Per-symbol page payload: company profile, dividend history, and
        the latest ~10 corporate announcements (title, date, type, and a
        pdf link into dps.psx.com.pk — a working PUCARS mirror). Returns:

            {
                "symbol": str,
                "company": dict | None,
                "dividends": [ {symbol, ex_date, payment_date,
                                record_date, amount, year}, ... ],
                "announcements": [ {title, date, posting_time,
                                    announcement_type, pdf_url}, ... ],
            }

        or None when the symbol is unknown or the fetch/parse failed.
        Dividends/announcements are [] (not None) when the symbol exists
        but has no rows — "empty" and "unavailable" stay distinguishable.
        """
        symbol = symbol.upper()
        node = await self._get_page_node(
            f"/symbol/{symbol}/__data.json?market=REG&x-sveltekit-invalidated=01"
        )
        if node is None:
            return None
        if "announcementsData" not in node and "companyData" not in node:
            logger.warning(
                f"PSX Terminal has no symbol page for {symbol}: "
                f"{node.get('error', 'unexpected payload keys: ') or list(node)}"
            )
            return None

        announcements = []
        raw_announcements = node.get("announcementsData")
        if isinstance(raw_announcements, list):
            for item in raw_announcements:
                if not isinstance(item, dict):
                    continue
                # Homepage feed wraps rows as {"d": {...}}; the symbol
                # page serves them flat. Accept both.
                row = item.get("d", item)
                if not isinstance(row, dict) or not row.get("title"):
                    continue
                pdf_url = row.get("pdf_id")
                if pdf_url and not str(pdf_url).startswith("http"):
                    pdf_url = None
                announcements.append(
                    {
                        "title": str(row["title"]).strip(),
                        "date": row.get("date"),
                        "posting_time": row.get("posting_time"),
                        "announcement_type": row.get("announcement_type"),
                        "pdf_url": pdf_url,
                    }
                )
        else:
            logger.warning(
                f"PSX Terminal announcementsData missing for {symbol}"
            )

        dividends = node.get("dividendsData")
        if not isinstance(dividends, list):
            logger.warning(f"PSX Terminal dividendsData missing for {symbol}")
            dividends = []

        company = node.get("companyData")
        if not isinstance(company, dict):
            logger.warning(f"PSX Terminal companyData missing for {symbol}")
            company = None

        return {
            "symbol": symbol,
            "company": company,
            "dividends": dividends,
            "announcements": announcements,
        }


def _as_float(value: Any) -> Optional[float]:
    """float() that returns None for None/unparseable instead of raising."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
