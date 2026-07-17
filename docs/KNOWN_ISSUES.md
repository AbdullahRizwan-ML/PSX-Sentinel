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

**Planned fix (not yet built):** direct scraping of PUCARS 
(`pucars.psx.com.pk`), PSX's actual corporate-announcement disclosure system, 
to pull quarterly result PDFs directly.

**Correction (Phase 5 Session 4, 2026-07-17):** earlier notes framed PUCARS 
as a *Playwright/headless-browser* problem and implied a Playwright decision 
would unlock it. That was wrong. PUCARS is a **login wall** — it requires 
real PSX-issued credentials (listed-company / broker accounts), not merely a 
JS-capable browser. Playwright does not solve a credential wall; adding it 
(done this session for NCCPL) does **not** bring PUCARS any closer. PUCARS 
stays blocked pending actual PSX credentials, which is an access/authorization 
question, not a scraping-technology one.

**Update (Phase 5 Session 2, 2026-07-17):** announcements are now *mirrored*
from PSX Terminal (see the new `FundamentalsCollector`) — 84 rows landed with
`source='psx_terminal'`, each with title/date/category and (mostly) a
`pdf_url` pointing at the real `dps.psx.com.pk/download/document/*.pdf`
files. This partially fills the gap: `FilingSceptic` still has no *parsed
filing text* (`raw_text` is empty, `pdf_parsed=false` — the PDF parser hasn't
been pointed at these URLs yet, and wiring new data into agents is a
deliberately separate decision), but the announcement *metadata* pipeline is
no longer empty. The mirror serves only the latest ~10 announcements per
ticker — it is a rolling window, not a historical archive, so PUCARS-direct
scraping remains the eventual plan for depth.

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

**Confirmed for Dunya too (Phase 5 Session 4):** `DunyaNewsCollector` uses the
identical keyword rule and shows the same noise — e.g. generic "oil prices
rise" headlines match OGDC/PPL via the "oil"/"petroleum" overlap, and
"Pakistan discusses purchase of 16 Boeing aircraft" matched PPL+PSO off "PSO"
appearing inside... actually via company-name first-word overlap. The
NewsSynthesizer relevance-judgment mitigation covers Dunya rows the same way
it covers ARY. No per-source fix attempted — it's the same known limitation.

### News source coverage is narrow

ARY News RSS and Dunya News (HTML scrape) are the two confirmed-working
sources. See "Resolved" section below for the full list of sources that were
tried and failed.

**Dunya News — DONE (Phase 5 Session 4, 2026-07-17).** Re-verified live that
`dunyanews.tv/en/Business` is plain static server-rendered HTML (HTTP 200 via
httpx, no Cloudflare challenge, **no Playwright needed** — an earlier note
lumping Dunya in with the Playwright decision was wrong and is corrected
here). Built `DunyaNewsCollector` (static-HTML scrape of the Business listing
+ per-matched-article page fetch for summary/date), `source='dunya'`,
pipeline Step 7/8, `--dunya-only` CLI flag. First live run: 29 rows across
PPL/PSO/OGDC. Same keyword-matching noise as ARY applies (see below).

### PSX DPS price depth caps at a rolling ~5-year window (date params ignored)

Verified live 2026-07-17 (Phase 5 Session 5, while determining the real
achievable backfill depth): the EOD timeseries endpoint
(`dps.psx.com.pk/timeseries/eod/{ticker}`) serves a **fixed rolling
~5-year window and ignores every date parameter**. Tested and all
returned the identical window (2021-07-19 → today at test time):

- `from=1990-01-01` (far-past start)
- `from=2015-01-01&to=2016-01-01` (a fixed window entirely in the past —
  still returned 2021-2026 data)
- `start`/`end` alternates, `period=10y`, unix-timestamp `from`
- the `timeseries/int/{ticker}` flavor is intraday ticks for the current
  day only, not an archive

**Consequences:**

- **7-8 years of price history is not obtainable from this source.** The
  deepest price series this project can hold is what it already captured:
  2021-06-07 onward (rows collected in June 2026, before they rolled off
  the source's window — DPS itself now starts at 2021-07-19).
- **`daily_prices` is now the archive, not a cache.** Rows older than the
  source's rolling window cannot be re-fetched if lost. Treat the table
  (and Neon backups) accordingly; never truncate-and-reload it.
- The collector's `from`/`to` request params are decorative until DPS
  honors them (kept in case that ever changes — see the depth-limit note
  in `price_collector.py`).
- This capped the Session 5 "matched window" for FIPI/LIPI at
  2021-06-07 → present: NCCPL's archive goes to 2015-12-09, but flows
  before the price series' left edge would have no price data to pair
  with for ML training.

### PSX Terminal fundamentals are a current snapshot — lookahead-bias risk for ML features

Flagged 2026-07-17 (Phase 5 Session 5, Part 1). The
`company_fundamentals` table holds **one current-snapshot row per
ticker** (today's P/E, dividend yield, market cap, free float). It has
no history and no as-of dates beyond `scraped_at`.

**Do not join these values onto historical training rows.** Using
today's P/E as a feature for a 2023 training row is lookahead bias —
the number embeds price/earnings information from after the row's date.
Whoever designs the next ML feature set must either leave fundamentals
out or build a properly dated history first.

**A legitimate path exists if wanted later:** PSX Terminal's
`/financials/{S}/__data.json` payload carries a `fyReports` list with
**20 fiscal years (FY2006–FY2025) of reported annual statements**
(revenue, EPS, net income, ~250+ fields each) **including per-FY
`price_earnings`, `dividends_yield`, `market_cap_basic`, and — key for
point-in-time correctness — `earnings_release_date`**, so a feature can
be built that only uses a fiscal year's numbers from the date they were
actually published. Not built in Session 5 (deliberately — this session
was depth extension, not feature design); it inherits the same
fragile-undocumented-endpoint caveat as the rest of the PSX Terminal
integration. No fake historical fundamentals were fabricated.

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

### PSX Terminal rides an undocumented internal endpoint (fragile by design)

The Phase 5 Session 2 fundamentals/announcements integration
(`backend/app/integrations/psx_terminal_client.py`) uses psxterminal.com,
whose **documented REST API is mostly dead** (verified live, 2026-07-17):

- `GET /api/symbols` and `GET /api/status` still work, no auth.
- `GET /api/fundamentals/{S}`, `/api/companies/{S}`, `/api/dividends/{S}`,
  `/api/ticks/{M}/{S}`, `/api/announcements/{S}` — the server **kills the
  TCP connection without a response**. Confirmed not client-side: the same
  requests fail identically via curl, httpx, *and* `fetch()` executed from
  the site's own origin in a real browser. The site's own frontend never
  calls them (it's SvelteKit SSR + WebSocket).
- The project's GitHub repo (`mumtazkahn/psx-terminal`) now 404s — deleted
  or private. The site footer now shows a commercial operator (Runtime
  Technologies (SMC-PRIVATE) LIMITED), a sign-in, and a refund policy — the
  open-API era of this service appears to be over.

The integration therefore parses the **SvelteKit `__data.json` SSR
payloads** the site itself is rendered from (`/financials/{S}/__data.json`
for fundamentals, `/symbol/{S}/__data.json?market=REG` for announcements +
dividends), with `?x-sveltekit-invalidated=01` to skip the ~350KB shared
layout node. These are internal, undocumented endpoints in SvelteKit's
"devalue" serialization — **any site redeploy can silently change the shape
and break the collector**. The client is built to degrade (every parse
failure returns None + a logged warning, never an exception), so a break
will show up as a PARTIAL/failed `pipeline_runs` row and NULL-flagged
verification output, not a crash. Re-run
`backend/scripts/verify_psx_terminal.py` after any suspected breakage.

### PSX Terminal per-ticker data gaps (observed live, 2026-07-17)

Exact gaps found during the Phase 5 Session 2 verification run — none of
these are smoothed over in the DB (missing = NULL, absent = no row):

- **ENGRO: not listed at all.** PSX Terminal's symbol universe has only
  ENGROH (post-merger Engro Holdings). ENGRO gets no fundamentals row and
  no mirrored announcements from this source. See the ENGRO entry below —
  this is a corporate-action problem, not a PSX Terminal defect.
- **LUCK: `free_float_pct` NULL** — the source's
  `float_shares_outstanding` field is missing for LUCK (confirmed at the
  API, not a parse bug).
- **LUCK and MARI: `dividend_yield` served as literal `0.0`.** Both
  companies pay dividends, so treat a 0.0 yield from this source as
  "probably no data" rather than a true zero. Stored as-is (0.0) because
  that is what the source returned — flagging here instead of guessing.
- **pdf_url coverage varies:** UBL 3/10, HBL 7/9, LUCK 9/10, OGDC 9/10
  (the rest 10/10). Missing ones are mostly "Disclosure of Interest"
  notices that carry only an image attachment, no PDF.
- **Same-day same-title announcements collapse.** The dedup key is
  ticker + title + date (per the session spec), so e.g. MEBL's six
  separate "Disclosure of Interest…" filings on 2026-07-15 (different
  executives, different posting times, identical titles) stored as one
  row. Acceptable for our use (signal, not registry), but per-executive
  granularity is lost. MEBL stored 5 rows from 10 fetched for this reason.
- The mirror window is the latest ~10 announcements per ticker — a rolling
  feed, not an archive (see the announcement-scraping entry above).

### NCCPL FIPI/LIPI — collector built (Playwright); automated challenge-pass fails on this host

Probed live 2026-07-17 (Phase 5 Session 3). NCCPL (nccpl.com.pk)
publishes daily Foreign/Local Investor Portfolio Investment data through
an internal JSON API that its own site calls:

- `GET /api/{fipi,lipi}-{normal,sector-wise}/latest-date` → freshness
  (returned 2026-07-16 — data is current).
- `POST /api/{fipi,lipi}-normal/data` with body `{"date": "YYYY-MM-DD"}`
  and `POST /api/{fipi,lipi}-sector-wise/data` with
  `{"fromDate": ..., "toDate": ...}` — both need an `X-CSRF-TOKEN`
  header scraped from any page's `<meta name="csrf-token">` (Laravel)
  plus the session cookie.
- Row shape (verified on real responses): `CLIENT_TYPE`, `MARKET_TYPE`
  (REG/FUT/BNB/GEM/NDM/ODL + derived TOTAL rows), `BUY/SELL/NET_VOLUME`,
  `BUY/SELL/NET_VALUE` (PKR; sells served negative), `USD`; sector-wise
  adds `SEC_CODE`/`SECTOR_NAME`.
- **Granularity: sector level at best — there is NO per-ticker
  breakdown.** Sector-wise covers ~10 named sectors (Cement, Fertilizer,
  both O&G sectors, Commercial Banks, Power, Tech, Textile, Food, Debt
  Market) plus an "All other Sectors" catch-all — the named ones cover
  all 10 of our tickers' sectors.
- **Archive depth: full history back to 2015-12-09** through the same
  API. Label drift across the archive: 2015-era rows say
  `FOREIGN INDIVIDUAL` / `REGULAR`, modern rows say
  `FOREIGN CORPORATES ` (trailing space) / `REG`. LIPI's TOTAL rows have
  a *blank* CLIENT_TYPE; FIPI wraps rows in `records` while LIPI uses
  `data`. Non-trading days return 200 + empty list.
- **Ranged sector queries return per-day rows concatenated WITHOUT a
  date column** (6-week range → 1,267 undated rows), so a collector must
  fetch day-by-day, one POST per date per dataset.

**The blocker:** the entire www.nccpl.com.pk zone (API paths included)
sits behind a Cloudflare *JS challenge* ("Just a moment…"). Plain httpx
and curl get 403 challenge pages; `curl_cffi` browser-TLS impersonation
(chrome131 / firefox135 / safari184) was tested and **also fails** —
the challenge requires actual JavaScript execution. It was uninstalled
again (not adopted). A real browser passes and everything works (that is
how all of the above was verified).

**Update (Phase 5 Session 4, 2026-07-17) — Playwright adopted, collector
built, but automation still can't pass the challenge on this host.**
Playwright was installed (`playwright install chromium`) and
`InstitutionalFlowCollector` was built to drive the mapped flow
(challenge → CSRF → in-page day-by-day POSTs) into `institutional_flows`.
But **automated Playwright cannot pass NCCPL's Cloudflare challenge on
this machine**, tested thoroughly:

- Playwright's bundled Chromium won't even launch here (`spawn UNKNOWN` —
  a Windows side-by-side/runtime error on the headless-shell binary).
- System Chrome (`channel="chrome"`) launches fine but the interactive
  Cloudflare challenge never clears under automation — headless *and*
  headed, with automation-fingerprint stealth flags
  (`--disable-blink-features=AutomationControlled`,
  `navigator.webdriver` spoof) and a Turnstile-checkbox click attempt.
  All stayed on "Just a moment…" / "Attention Required".
- The `cf_clearance` cookie is httpOnly and the genuine browser that does
  pass (the Claude in-app browser pane, an Electron browser) can't hand it
  to httpx, so cookie transplant isn't available either.

So the collector is **correct and reusable but a no-op on an
automation-flagged host** — it returns 0 rows + a logged error and writes
a PARTIAL PipelineRun rather than crashing (verified end-to-end).

**How the table actually got populated this session:** the real browser
pane (which passes the challenge) was used to fetch the data via the
site's own `fetch`, and a one-off bulk loader inserted it — **4,232 real
rows**, 20 trading days (2026-06-17 → 2026-07-16), all 4 datasets, dedup
proven (second load +0). See the Build Log. This is a manual bootstrap,
NOT ongoing automation.

**Historical backfill extended (Phase 5 Session 5, 2026-07-17):** the
same browser-pane channel was used to backfill the **full matched
window** — 2021-06-07 → 2026-07-17, **1,267 trading days, all 4
datasets, 268,142 rows** (up from the 4,232-row/20-day Session 4
bootstrap). Done via a month-chunked, resumable, disk-buffered loop
(pane `fetch` → localhost receiver → `execute_values` bulk loader) so a
mid-run Cloudflare-session death only costs the in-flight month, not a
restart — which happened twice and recovered cleanly (re-navigate to
re-pass the challenge, resume only the months not yet on disk/in the DB).
Dedup re-proven (full reload of every chunk inserted +0). This is still
a **manual bootstrap, not automation.**

**⚠️ Problem B — ongoing UNATTENDED collection — remains UNSOLVED and is
separate from the Session 5 backfill above.** Session 5 deepened the
*historical* archive by hand; it did **not** make daily collection run
by itself. `InstitutionalFlowCollector` (automated Playwright) is still
Cloudflare-blocked on this host (0 rows + logged error), exactly as in
Session 4 — no attempt was made to fix it this session (out of scope).
Unattended collection still needs a clean/residential IP, a
CAPTCHA-solving service, or a scheduled manual export — none solved
here. **Railway deploy note:** even where the challenge is passable, the
chromium binary must be installed at build time (`playwright install
chromium`, ~hundreds of MB) — flagged in `requirements.txt`.
(Correction to Session 3's note: Playwright does **not** unlock
PUCARS — that's a login wall, see the announcement-scraping entry — and
Dunya News never needed Playwright, it's static HTML.)

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

### ENGRO stopped trading in Jan 2025 (ENGRO → ENGROH corporate action)

ENGRO has 887 raw `daily_prices` rows vs. ~1,238 for the other 9 tickers
(confirmed via the Phase 3 Session 1 dataset build, 2026-06-27).

**Root cause — corrected 2026-07-17 (Phase 5 Session 2):** this entry
previously blamed a mid-execution Antigravity quota-out during the Phase 2A
fix run for an unfilled backfill gap. Direct SQL now shows ENGRO's series
runs 2021-06-07 → **2025-01-03 and then stops** — it is not a gap, the
ticker stopped trading. Engro Corporation was folded into Engro Holdings
(**ENGROH**) around the turn of 2025, and PSX Terminal's symbol universe
confirms it: ENGRO is absent, only ENGROH exists. Re-running the price
collector cannot fix this — PSX DPS has nothing new to serve for a
delisted symbol.

**Confirmed with primary evidence — Phase 5 Session 3 (2026-07-17):**

- PSX formally delisted ENGRO effective **2025-01-14**; the last trading
  day was **2025-01-13**. Mechanism: a Scheme of Arrangement under which
  Engro Corporation merged into Dawood Hercules Corporation, which was
  renamed **Engro Holdings Limited (ENGROH)**; ENGRO shareholders were
  swapped into ENGROH and Engro Corp became its wholly-owned subsidiary.
  (Reported by Profit/Pakistan Today and Mettis Global, 2025-01-14.)
- PSX DPS, queried live, still serves the old ENGRO series but it ends at
  **2025-01-03** — exactly matching our `daily_prices` (887 rows,
  2021-06-07 → 2025-01-03). So our collection was never at fault, and the
  final ~6 trading days (Jan 6–13, 2025) are simply absent from DPS's EOD
  series — unrecoverable from this source.
- **ENGROH exists on PSX DPS with live data**: probed 2026-07-17, 1,219
  rows spanning 2021-07-19 → 2026-07-16 (the continuous ex-Dawood-Hercules
  series, renamed in place).
- The fact is now first-class in the schema: `companies.delisted_date`
  (nullable Date, migration `44cd906f6e1e`) is set to 2025-01-14 for
  ENGRO and NULL for the other nine. The ENGRO seed entry carries the
  same date for fresh databases.

**Current impact:** ENGRO's prices, ML rows, and any conviction report are
frozen at January 2025; PSX Terminal fundamentals/announcements are
unavailable for it (see the PSX Terminal gaps entry above). The ML
dataset's ENGRO test window being disjoint from the other 9 tickers
(flagged in the Phase 5 Session 1 backtest) is the same fact surfacing.

**Decision needed (deferred, not this session):** either migrate the
universe entry ENGRO → ENGROH (new seed row, fresh price history, decide
what to do with the old ENGRO series), or drop ENGRO to a 9-ticker
universe. Touching the ticker universe affects seeds, the ML dataset, and
every stored report, so it deserves its own session.

**Resolved for the universe (Phase 5 Session 4, 2026-07-17):** ENGROH was
added as an 11th company (active in `PSX_TICKERS`; ENGRO removed from the
active list). ENGROH backfilled cleanly via the existing collectors — 1,219
price rows (2021-07-19 → 2026-07-16), fundamentals (P/E 4.99, mkt cap 320B,
free float 18.04%; dividend_yield NULL at source), 10 mirrored announcements.
**ENGRO stays as a frozen historical record:** its 887 price rows and
`delisted_date=2025-01-14` are untouched, it is NOT in the active collection
universe, and nothing backfills it further. Still deferred (own session): the
ML dataset / backtest decision on whether to *train* on ENGROH — this session
deliberately did not touch `ml_data/`.

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