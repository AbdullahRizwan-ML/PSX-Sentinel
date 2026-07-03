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
- `ml_contribution` is wired (Phase 3 Session 3, 2026-06-27) but
  currently always 0.0 in production because no ticker's max
  `predict_proba` clears the 0.55 confidence gate. The Session 2
  evaluation already foreshadowed this (model accuracy +6pp over
  random, structurally FLAT-blind), and live production confirms it:
  all 10 tickers' top-class probability is in [0.357, 0.407]. This is
  the *designed* behavior of the gate — silence rather than a weak
  vote — not a defect. `score_breakdown.ml_detail` records the gate
  status, predicted class, and per-class probabilities even when
  `ml_contribution=0`, so downstream consumers can distinguish "below
  gate" from "model unavailable" from "insufficient history". Until
  the model is retrained on a stronger feature set or class-balanced
  better, expect this term to keep contributing 0 in production.
  **Update (Phase 4 Session 2, 2026-06-27):** the invisibility half of
  this is now fixed — `score_breakdown.ml_detail` (including all 3
  class probabilities and the gate/skip_reason status) is exposed on
  `GET /companies/{ticker}/report` and rendered on the company-detail
  page via `MlSignalCardRich`. So a user can now actually *see* that
  the model is evaluating each ticker and consistently landing just
  below the gate, rather than the term just silently reading 0 with
  no explanation. The underlying model is still weak (same 39%
  accuracy, same gate, same near-0.4 probability cluster) — only the
  visibility changed, not the prediction quality.

**Expected to ease once:** PUCARS scraping produces real filing data
(lets `filing_contribution` actually vary), and/or a future ML
retraining lifts at least some tickers past the 0.55 gate. Flagging
this now so a clustered 58.5-ish score across many tickers isn't
mistaken for a future regression — it's the expected output of the
current formula with two of its four terms still structurally pinned
to zero (filing always, ML almost always).

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

---

## Resolved

### PriceChart doesn't re-theme on dark-mode toggle — RESOLVED 2026-07-04

**Was:** `frontend/src/components/price-chart.tsx` kept whatever theme was
active at mount time until the page was reloaded. The chart resolved its
colors (line/axis/grid/text) by reading CSS custom properties via
`getComputedStyle(document.documentElement)` inside a `useEffect` keyed only
on `[prices]`. A theme toggle changes the `.dark` class on `<html>` but not
`prices`, so the effect never re-ran and the lightweight-charts canvas
retained its mount-time colors. The visible symptom (confirmed by manual
testing before the fix): after a dark→light toggle the axis/price/volume
text stayed light and was unreadable against the light card background.

**Diagnosis (Phase hotfix, 2026-07-04):** confirmed this was toggle-staleness
only, NOT a genuinely wrong light-mode color value — a *fresh* page load in
light mode rendered dark, fully-legible axis/price/volume text (the light
`--foreground` token `195 30% 12%` is correct dark-on-cream). So only the
already-mounted-then-toggled path was broken.

**Fix:** `price-chart.tsx` now consumes `useTheme()` and re-themes the
existing chart **in place** on toggle — no recreate — via a new effect keyed
on `[theme]`. lightweight-charts v5's `chart.applyOptions()` /
`series.applyOptions()` update an existing instance's colors in place
(layout `textColor`, pane separators, grid, crosshair, the area line color +
fill + price line, and both MA line colors); the volume histogram's per-bar
colors are baked into each data point, so those are re-pushed via
`volumeSeries.setData()` with freshly-resolved directional tints. Color
resolution was extracted into a shared `resolveChartColors()` helper so the
create path and the re-theme path can't drift. Because the chart is updated
in place rather than recreated, the user's current visible range / zoom is
preserved across a toggle (a recreate would have reset it to `fitContent`).
The first run of the theme effect is skipped (a ref guard) since effect #2
already created the chart with the current theme's colors.

**Verified (Claude Preview MCP, 2026-07-04):** screenshotted all four states
on `/companies/PPL` — fresh load light, fresh load dark, live dark→light
toggle, live light→dark toggle — axis/price/volume text legible in every
state, 6M range preserved across both live toggles, no console errors.
`npx tsc --noEmit` exits 0.

For contrast, `ConvictionDial` never had this problem: it passes
`hsl(var(--token))` *strings* straight into SVG `stroke`/`fill`, which the
browser re-resolves at paint time, so it re-themes instantly with no JS.

### Unadjusted stock-split rows in daily_prices — RESOLVED 2026-06-27

**Was:** One row produced a ~-88% forward 5-day return in the Phase 3
Session 1 dataset build. Cause: stock splits / face-value reductions are
not applied in PSX DPS raw close prices.

**Fix (Phase 3 Session 2):** A diagnostic pass over the live DB
(`backend/scripts/find_split_row.py`) identified three split-shaped
overnight gaps:

| Ticker | Effective date | Pre-split close | Post-split close | Empirical ratio |
|---|---|---|---|---|
| MARI | 2024-09-16 | 3560.00 | 415.90 | 8.5597 (likely 10:1) |
| LUCK | 2025-04-28 | 1748.80 | 365.00 | 4.7912 (likely 5:1) |
| UBL  | 2025-06-23 | 522.79  | 259.99 | 2.0108 (clean 2:1) |

All three were confirmed as splits, not market moves, by the dollar-volume
preservation signature (post-split day's `close × volume` ≈ pre-split day's
`close × volume`), the absence of any matching bad-news article in
`news_articles`, and the fact that no other near-comparable single-day
drops exist in the entire 12k-row history (next-worst non-split drop is
-25%, well below the -50%/-80%/-88% split signatures).

The fix is a backward-adjustment table in
`backend/app/ml/split_adjustments.py`. The ML dataset builder applies it
before the feature build, dividing pre-split open/high/low/close by the
empirical ratio and multiplying volume by it. Empirical (not clean) ratios
are used so the post-adjustment series is exactly continuous across the
split day — see the module docstring for the trade-off discussion (cost:
any genuine same-day movement on the split day is absorbed into the
adjustment factor; for MARI that's ~+17% absorbed, LUCK ~-4%, UBL ~0%).

Verified post-fix (`backend/scripts/verify_dataset.py`): worst
`forward_return_5d` in the entire labeled dataset is now -25.4% (OGDC,
2024-02-12 — a genuine bad-news day), no more -50% to -88% rows. The
per-ticker chronological-split invariant still holds for all 10 tickers.
Row counts unchanged from Session 1 (train 6621 / val 1418 / test 1426),
class distributions shifted by ≤0.4pp.

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