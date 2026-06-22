# Known Issues / Deferred Work

> When an issue here gets fixed, don't delete the entry — move it to the 
> "Resolved" section at the bottom with the date and what fixed it. This keeps 
> a record of dead ends so nobody (human or AI) re-attempts something that's 
> already been tried and confirmed not to work.

---

## Open issues

### Announcement scraping (PSX portal) — not working

`dps.psx.com.pk/announcements` is JavaScript-rendered. Both the static HTML 
scraper and the JSON API attempt (`dps.psx.com.pk/data/announcements`, 404) 
return nothing. `AnnouncementCollector` currently runs cleanly and logs 0 
results — this is graceful, not a crash, but it means the `FilingSceptic` 
agent (Phase 2B) has no real filing text to analyze yet and is designed to 
report low confidence and skip its LLM call rather than fabricate findings.

**Planned fix (not yet built):** Playwright-based scraper targeting PUCARS 
(`pucars.psx.com.pk`), PSX's actual corporate-announcement disclosure system, 
to pull quarterly result PDFs directly. This is higher-effort than the 
current scrapers (needs a real headless browser, not just `httpx`) and was 
deliberately deferred rather than rushed.

### News-to-ticker matching is noisy

Keyword matching (ticker symbol or first word of company name appearing in 
headline/summary text) produces false-positive matches — e.g., general 
"petroleum levy" or oil-price headlines get attached to PPL and PSO purely 
because "petroleum" appears in both the headline and the company name.

**Mitigation in place (not a true fix):** The Phase 2B `NewsSynthesizer` 
agent's prompt explicitly instructs the LLM to judge whether each headline is 
genuinely about the specific company or just a tangential keyword hit, and to 
weight its sentiment analysis accordingly, reporting a `RELEVANT_ARTICLES` 
count separately from the raw matched-article count.

### News source coverage is narrow

Only ARY News RSS is confirmed working. See "Resolved" section below for the 
full list of sources that were tried and failed.

**Possible future addition:** Dunya News's actual website (the business 
section at `dunyanews.tv/en/Business`) loads without Cloudflare blocking, 
unlike their RSS feed. Could be scraped directly (HTML, not RSS) as an 
additional source. Not yet built — flagged as a nice-to-have, not blocking.

### Filing/fundamentals data is currently approximate

The PSX DPS timeseries endpoint does not provide intraday high/low — only 
open, close, and volume per day. `high` and `low` are currently *derived* as 
`max(open, close)` and `min(open, close)`, which is an approximation, not the 
real intraday range. Acceptable for the technical-analysis agent's purposes 
(moving averages, momentum, RSI all use close price primarily) but worth 
remembering if any feature ever specifically needs true intraday range.

### CapitalStake API — not pursued

CapitalStake is PSX's actual data vendor (confirmed via the "Data powered by 
Capital Stake" footer on the live PSX portal) and would provide proper 
fundamentals (P/E, EPS, dividend yield, insider transactions, an 
announcements mirror). Their website only advertises a consultation-based 
commercial API — no visible self-serve signup or free-tier key.

**Decision:** not pursuing this right now. Revisit only if/when the project 
moves toward having real paying subscribers, at which point a commercial data 
contract makes more sense anyway.

### ML model contribution to Arbitrator score is a placeholder

The `Arbitrator` agent's weighted scoring formula (Phase 2B) reserves a 15% 
weight slot for an ML earnings-prediction signal, but Phase 3 (the actual 
XGBoost/LightGBM model, adapted from EarningsPulse) hasn't been built yet. 
Until then, that term contributes 0 to the score — the formula is structured 
to make plugging it in later straightforward, but the conviction scores 
produced by Phase 2B alone are necessarily incomplete.

---

## Resolved

### yfinance — RESOLVED 2026-06-08 (removed permanently)

**Was:** Primary price data source via `.KA`-suffixed tickers (e.g. 
`ENGRO.KA`). Failed for every tested ticker with "No timezone found, symbol 
may be delisted" — Yahoo Finance blocks or doesn't properly support PSX 
tickers (Cloudflare-related).

**Fix:** Replaced entirely with the PSX DPS timeseries endpoint 
(`dps.psx.com.pk/timeseries/eod/{ticker}`), confirmed working with no 
authentication required. `yfinance` removed from `requirements.txt` — do not 
reintroduce it for PSX data.

### Dawn Business RSS, Business Recorder RSS, Profit (Pakistan Today) RSS — RESOLVED 2026-06-08 (removed)

**Was:** Original 3-source news RSS list. All three returned `bozo=1` 
(malformed XML) on first pipeline run; manual follow-up testing showed Dawn 
specifically returns HTTP 403 (Cloudflare-blocked).

**Fix:** Replaced with ARY News RSS as the sole confirmed-working source. 
Also tried and rejected during manual testing: Geo.tv RSS (redirects to a 
regular webpage, not raw XML), The News RSS (returns an empty "no news print 
today" placeholder), Dunya News RSS (also redirects to a webpage, 
Cloudflare-blocked) — none of these three were usable as RSS, though Dunya 
News's actual website was separately confirmed loadable (see Open Issues 
above re: possible future non-RSS scraping).

### bcrypt / passlib incompatibility — RESOLVED 2026-05-30

**Was:** `passlib==1.7.4`'s `detect_wrap_bug` check fails against 
`bcrypt>=4.1`, causing every password hash/verify call to error out.

**Fix:** Pinned `bcrypt==4.0.1` explicitly in `requirements.txt`. Verified 
working end-to-end (register/login/JWT flow tested live).

### Neon Cloud SSL connection string incompatible with asyncpg — RESOLVED 2026-05-30

**Was:** `DATABASE_URL` query params `sslmode=require&channel_binding=require` 
(Neon's default connection string format) aren't accepted directly by 
asyncpg's URL parser, causing the app to fail on startup.

**Fix:** `session.py` was patched to strip those specific query params from 
the URL and instead pass SSL configuration via SQLAlchemy's `connect_args`. 
The separate sync engine (used only for Alembic migrations, via psycopg2) 
handles the original connection string natively and didn't need this fix.

### Missing `email-validator` and `psycopg2-binary` packages — RESOLVED 2026-05-29/30

**Was:** Pydantic's `EmailStr` type silently requires `email-validator` to be 
installed, and Alembic's sync engine requires `psycopg2-binary` — neither was 
in the initial `requirements.txt` generated in Phase 1A.

**Fix:** Both added to `requirements.txt` once the missing-dependency errors 
surfaced during Phase 1B testing.