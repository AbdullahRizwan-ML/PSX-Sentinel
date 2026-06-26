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

### Conviction scores currently cluster near 50-60 for most tickers

Live verification of Phase 2B Session 2's `AnalysisOrchestrator` (see Build
Log, 2026-06-23) showed both test tickers (PPL, MCB) producing an identical
conviction score of 58.5, despite very different underlying data (PPL had 9
matched news articles, MCB had 0). This is correct behavior given the current
scoring formula and data conditions, not a bug — but the underlying cause is
worth tracking as an open issue.

`technical_contribution` is currently the only scoring term that is
consistently nonzero, because:
- `news_contribution` is frequently 0.0, but for two different reasons that
  currently look identical in the final score: a genuine NEUTRAL sentiment
  judgment from `NewsSynthesizer` (it called the LLM and judged the articles
  as not meaningfully bullish/bearish — see the noisy-keyword-matching issue
  above) and an outright skip when there are zero articles to analyze. The
  formula can't currently distinguish "real neutral signal" from "no signal
  at all."
- `filing_contribution` is always 0.0 right now, since `FilingSceptic` always
  skips its LLM call until PUCARS scraping (see Announcement scraping issue
  above) produces real filing text for any ticker.
- `ml_contribution` is hardcoded to 0.0 — Phase 3 hasn't been built yet (see
  ML model contribution issue below).

**Expected to ease once:** Phase 3's ML signal goes live (gives a second
consistently-nonzero term) and/or PUCARS scraping produces real filing data
(lets `filing_contribution` actually vary). Flagging this now so a clustered
58.5-ish score across many tickers isn't mistaken for a future regression —
it's the expected output of the current formula with two of its four terms
structurally pinned to zero.

### ENGRO has shorter price history than other tickers

ENGRO has 887 raw `daily_prices` rows vs. ~1,238 for the other 9 tickers
(confirmed via the Phase 3 Session 1 dataset build, 2026-06-27).

**Root cause:** the mid-execution Antigravity session quota-out during the
Phase 2A fix run (see Build Log, 2026-06-07/08 entry) — the recovery run
never went back and backfilled ENGRO's missing date range.

**Current impact:** handled gracefully by Phase 3's per-ticker chronological
split (ENGRO just gets proportionally fewer train/val/test rows), so it is
not currently breaking anything. But it means ENGRO is trained/tested on a
narrower history than every other ticker, which could make any
ENGRO-specific model evaluation less reliable than for other tickers.

**Suggested fix (not yet done):** re-run the price collector specifically
for ENGRO against the PSX DPS endpoint to backfill the missing date range,
then re-verify row count via direct SQL before rebuilding the ML dataset.

### Suspected unadjusted stock-split row in daily_prices

One row produced a ~-88% forward 5-day return in the Phase 3 Session 1
dataset build (2026-06-27) — almost certainly a stock split that wasn't
adjusted for in the underlying price series, not a real market move.

**Not yet identified:** which ticker/date this is. Not yet fixed.

**Important:** an unadjusted split would corrupt more than just that one
forward-return label — it would also distort `return_1m`/`return_3m`/moving
averages for that ticker in the weeks surrounding the split date, since
those features look backward across the same discontinuity.

**Suggested fix (Phase 3 Session 2 or a dedicated data-quality pass):**
identify the exact ticker/date via a query for extreme single-day or 5-day
returns across `daily_prices`, confirm whether it's a real split via a
news/filing search, and either apply a split adjustment to the price series
or explicitly filter/winsorize the affected window — don't just clip the one
extreme label and leave the upstream features contaminated.

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