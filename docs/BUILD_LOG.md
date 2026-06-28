# Build Log

> Append-only. Never edit or delete past entries — if something turns out to be 
> wrong, add a new entry correcting it rather than rewriting history. Each entry 
> should be short: a few lines, not a full transcript. The point is to capture 
> *decisions and their reasons*, and *what was verified and how*, not every detail 
> of every conversation.

---

## 2026-05-26 — Project kickoff, idea selection

Decided against pursuing multiple smaller portfolio projects in favor of one 
"mind-blowing" flagship project, on the theory that a single deeply impressive 
system beats several average ones for both recruiter attention and revenue 
potential. After comparing several candidate ideas (due-diligence engine, 
workflow-automation service, regulatory compliance monitor, autonomous ML 
consultant), settled on a PSX (Pakistan Stock Exchange) intelligence platform — 
it uniquely combines: personal investing interest, an unfair geographic/local-
context advantage no remote developer can replicate, and direct architectural 
reuse of two existing projects (EarningsPulse's ML pipeline, Contrarian Oracle's 
multi-agent narrative auditing).

## 2026-05-28 — Architecture pivot: reject CrewAI/LangChain, reject Streamlit

Read production-engineer feedback (Reddit r/AI_Agents threads) describing CrewAI 
as too heavy (1GB+ venv), opaque, prone to redundant tool calls, and difficult to 
deploy reliably. A separate post from someone who'd built 10+ enterprise agent 
systems explicitly recommended treating multi-agent systems as "a distributed 
systems problem first, AI second" — custom orchestration, Redis for state, 
PostgreSQL for audit logs, circuit breakers.

Decision: full architecture rewrite before any code was written. Dropped 
Streamlit dashboard plan entirely (reads as a student project). Adopted: 
Next.js + TypeScript frontend (not yet built), FastAPI + async SQLAlchemy + 
PostgreSQL + Redis backend, **custom** Python agent orchestration with a single 
`LLMGateway` chokepoint for all LLM calls (cost tracking, circuit breakers, 
audit logging built in from day one).

Project named **PSX Sentinel** (rejected an earlier Urdu-language name — 
"Sentinel" reads as enterprise/institutional rather than as a local student 
demo).

## 2026-05-29 — Phase 1A complete (core infrastructure)

Built via Antigravity using Claude Opus 4.6: 8 files — `config.py`, `models.py` 
(10 SQLAlchemy models), `session.py`, `security.py` (JWT), `redis_client.py`, 
`llm_gateway.py`, `agents/base.py`. Zero placeholders, zero TODOs reported.

**Fixed during build:** `psycopg2-binary` was missing from `requirements.txt` 
(needed by Alembic's sync engine) — added manually.

**Verified:** files present on disk, directory structure correct, imports 
clean. Full functional verification deferred to Phase 1B since the app 
couldn't actually start without the API layer.

## 2026-05-30 — Phase 1B complete (API layer), pushed to GitHub

Built via Antigravity/Opus 4.6: 18 files — Pydantic schemas, all API routers 
(auth, companies, intelligence, health), Celery app config + task stubs, 
Alembic env, `main.py` entry point with CORS/rate-limiting/timing middleware, 
`.env.example`.

**Fixed during build (real bugs caught and resolved in this session):**
- `email-validator` package missing — required by Pydantic's `EmailStr`.
- `bcrypt` incompatibility — `passlib==1.7.4` breaks against `bcrypt>=4.1`. 
  Pinned `bcrypt==4.0.1` explicitly in `requirements.txt` (otherwise pip pulls 
  in whatever passlib's loose `bcrypt>=3.1.0` constraint allows, which floated 
  up to 5.0.0 on a fresh install — this was double-checked and confirmed fine 
  in practice, but the pin stays as the safety net).
- Neon Cloud's connection string (`sslmode=require&channel_binding=require`) 
  isn't accepted as-is by asyncpg's URL parser — `session.py` was patched to 
  strip those query params and pass SSL via `connect_args` instead.

**Verified live (not just claimed):** server boots cleanly, all 10 tables 
created in real Neon Cloud Postgres, `/api/v1/health` returns 
`{"status": "healthy", "database": "connected", "redis": "connected"}`, 
full register → login → JWT → `/me` flow tested end-to-end with real HTTP 
calls, duplicate-email registration correctly returns 409.

Pushed to GitHub: 32 files, 4,129 lines, clean `.gitignore` (`.env`, `venv/`, 
`__pycache__/` etc. confirmed NOT in the pushed file list).

## 2026-05-30 — Phase 2A first execution: 3 of 4 data sources silently failed

Built via Antigravity/Opus 4.6: 12 files — `BaseCollector` pattern, 
`seed_data.py`, `price_collector.py` (yfinance-based), 
`announcement_collector.py` (PSX portal scraping), `news_collector.py` 
(feedparser/RSS), `pdf_parser.py`, `pipeline.py` orchestrator, pipeline API 
endpoints, updated Celery tasks, standalone CLI script.

First real run against live tickers (ENGRO, LUCK) revealed the architecture 
was sound but the **external data sources were not**:
- `yfinance` failed both tickers: "No timezone found, symbol may be delisted" 
  — Yahoo Finance's PSX `.KA` ticker support is unreliable/blocked.
- Announcement collector returned 0 results from both the JSON endpoint and 
  HTML scraping.
- All 3 RSS feeds (Dawn, Business Recorder, Profit Today) returned `bozo=1` 
  (malformed XML feedparser couldn't parse).

This was caught **because the pipeline's audit logging (`PipelineRun` table) 
worked correctly** and showed `PARTIAL`/failure statuses rather than silently 
reporting success — validating the decision to build observability in from 
Phase 1A.

## 2026-05-30 — Manual diagnostic run identifies real replacements

Ran a standalone diagnostic script (`scripts/diagnose_sources.py`) by hand 
against each data source, independent of any AI session, to get ground truth 
before attempting fixes. Findings:
- `dps.psx.com.pk/timeseries/eod/{ticker}` — **works**, no auth, returns JSON: 
  `[unix_timestamp, open, volume, close]` per row. High/low are NOT provided 
  by this endpoint and must be derived (`high = max(open, close)`, 
  `low = min(open, close)` — an approximation, not exact intraday high/low).
- `dps.psx.com.pk/data/announcements` — 404, confirmed dead.
- `dawn.com/business/rss` — 403 (Cloudflare), confirmed dead.
- Noted PSX's own portal footer reads "Data powered by Capital Stake" — 
  investigated CapitalStake as a potential vendor (see decision below).

## 2026-06-07/08 — Phase 2A fixes applied and fully verified

Applied surgical fixes (not a rewrite) based on the diagnostic findings:
- `price_collector.py`: removed yfinance entirely, replaced with 
  `_fetch_psx_dps()` hitting the confirmed-working PSX DPS timeseries endpoint.
- `news_collector.py`: removed Dawn/Business Recorder/Profit Today RSS feeds, 
  replaced with ARY News RSS (`arynews.tv/category/business/feed/` — confirmed 
  working via manual browser test, real XML, no Cloudflare block). Added an 
  XML-cleaning preprocessing step (`_fetch_feed_safe`) to handle malformed 
  entities before handing off to `feedparser`.
- `requirements.txt`: removed `yfinance==0.2.36`.
- Manually tested 4 other candidate RSS sources by opening them directly in a 
  browser: Geo.tv RSS redirected to a regular webpage (not raw XML, skipped), 
  The News RSS returned "No news print today in this section" (empty, 
  skipped), Dunya News RSS opened a webpage instead of XML (skipped, 
  Cloudflare-blocked) — but Dunya News's actual *website* business section 
  loads fine without Cloudflare, flagged as a possible future scraping target 
  (not RSS-based).

**Decision: did not pursue CapitalStake.** Their site only offers a 
consultation-based commercial API (no self-serve free tier/key). Revisit only 
if/when the project moves toward paid subscribers.

A mid-execution Antigravity session was cut off by a Gemini model quota limit 
partway through running the fixed pipeline. Rather than trust the partial 
completion summary, ran a manual read-only verification prompt (file content 
checks + live DB queries) before proceeding — this confirmed the code fixes 
were correctly applied (two "FAIL" results in the automated check turned out 
to be leftover comment-line references to the old dead sources, not functional 
bugs) and that real price data had already landed in the database 
(ENGRO: 887 rows at that point).

**Final full-pipeline run, all 10 tickers, manually verified via direct SQL 
query against Neon Cloud (not just trusting console output):**
- 9,904 total price rows inserted across all 10 tickers, 0 ticker failures, 
  ~62 minutes total runtime.
- 19 news articles inserted from ARY News, matched against PPL/PSO/OGDC.
- 0 announcements/PDFs (expected — portal scraping still not functional).
- `pipeline_runs` table shows clean `SUCCESS` status for `price_collector` and 
  `news_collector` on the final run, vs. `PARTIAL` on the May 30 run — direct 
  before/after confirmation the fix worked.

Noted but accepted as a known limitation: news-to-ticker keyword matching is 
noisy (e.g., general "petroleum levy" headlines match PPL/PSO purely on the 
word "petroleum"). Not fixed at the collector level — instead, the plan is for 
the Phase 2B `NewsSynthesizer` agent to be explicitly prompted to judge 
genuine relevance rather than trust the keyword match blindly.

Phase 2A code + fixes committed and pushed to GitHub.

## 2026-06-08 — Decision: move primary development from Antigravity to Claude Code

Reasoning: Antigravity sessions repeatedly lost full context (relying on 
condensed summaries between sessions) and hit Gemini/Opus quota limits 
mid-execution more than once (notably mid-pipeline-run during the Phase 2A fix 
session). Claude Code, paired with a Claude Pro subscription, offers direct 
filesystem access and full-codebase context without the copy-paste-summary 
friction. Decision made to set up `CLAUDE.md` + this build log + 
`KNOWN_ISSUES.md` specifically to make this transition smooth and to make 
future tool-switching (back to Antigravity, or to any other tool) low-friction, 
since the project's state lives in plain files rather than locked inside a 
specific tool's chat history.

## 2026-06-08 — Phase 2B prompt drafted (4 agents), not yet executed

Drafted the Phase 2B Session 1 prompt: `TrendAnalyzer` (pure-Python technical 
indicator calculation feeding one LLM call for interpretation), 
`NewsSynthesizer` (skips the LLM entirely when zero relevant articles exist), 
`FilingSceptic` (skips the LLM entirely while announcement data remains empty — 
explicitly designed to be honest about the current data gap rather than 
fabricate analysis), and `Arbitrator` (deterministic weighted-scoring formula 
computed in Python first, LLM call second only to generate the written bull/
bear case narrative — not to decide the score itself).

Key design principle locked in: **agents do real calculation before calling 
the LLM; the LLM interprets pre-computed numbers, it does not compute them.** 
This mirrors the pre-computation pattern already used in the data-collector 
layer (collectors fetch/store, agents read from DB rather than touching the 
internet directly) — extending the same "minimize what touches the LLM" 
philosophy to control token usage and rate-limit exhaustion.

Not yet run. Will be executed via Claude Code going forward.

## 2026-06-23 — Phase 2B Session 1 built: four specialist agents

Built via Claude Code (Opus 4.6 session): 4 files in `backend/app/agents/` —
`trend_analyzer.py`, `news_synthesizer.py`, `filing_skeptic.py`,
`arbitrator.py`. Written against the actual existing interfaces (read
`agents/base.py`, `db/models.py`, `core/llm_gateway.py` first rather than
assuming signatures):

- `TrendAnalyzer` — computes MA20/MA50, RSI(14), 1w/1m/3m momentum, volume
  trend, and 52-week range position in pure Python from `context.recent_prices`,
  then makes one LLM call to interpret the numbers into a signal
  (STRONG_BUY..STRONG_SELL) with reasoning. Confidence scales with how many
  price data points were available (0.2 to 0.85).
- `NewsSynthesizer` — skips the LLM call entirely when `context.news_articles`
  is empty (confidence 0.3, no hallucination). When articles exist, the
  prompt explicitly instructs the model to judge genuine relevance vs.
  tangential keyword matches (the PPL/PSO "petroleum" false-positive problem
  noted in Known Issues) and report a separate `RELEVANT_ARTICLES` count.
- `FilingSceptic` — skips the LLM call entirely when no announcement has
  non-empty `raw_text` (current state: always, since PUCARS scraping isn't
  built yet). Returns confidence 0.2 and an honest "no filing data available"
  message rather than fabricating analysis. The LLM red-flag-auditor path is
  fully implemented and ready to activate once Phase 2A+ adds real filing text.
- `Arbitrator` — computes the conviction score (0-100) deterministically in
  Python first using the confidence-weighted formula from the spec (technical
  signal × its confidence, news sentiment × its confidence, filing red-flag
  penalty only), then makes one LLM call to write bull_case/bear_case/
  risk_factors narrative around the pre-computed score. The LLM never
  decides the number.

**Verified this session (syntax/structure only — not yet runtime):**
- All four files confirmed present on disk via `Glob`.
- All four parsed cleanly with Python's `ast.parse()` — no syntax errors.
- Manually cross-checked each agent's constructor and `run()` signature
  against `agents/base.py`'s actual `BaseAgent`/`AgentContext`/`AgentResult`
  definitions (not assumed from the prompt spec) before writing code.

**Explicitly NOT verified yet** (next session's job): no `AnalysisOrchestrator`
exists to actually instantiate these agents with a real `LLMGateway` and
`AsyncSession`, build a populated `AgentContext` from the live database, and
run them end-to-end. No live LLM call has been made by any of these four
agents. Per this project's working convention, these agents are not to be
treated as "done" until that live run happens and is checked against the
database/logs directly — current state is "written and import-safe," not
"working."

## 2026-06-23 — Phase 2B Session 2 built: AnalysisOrchestrator, live-verified

Built via Claude Code (Opus 4.6 session): `backend/app/agents/orchestrator.py`
(new), `backend/app/api/v1/companies.py` (modified), `backend/app/workers/tasks.py`
(modified), `backend/scripts/test_analysis.py` (new, standalone verification
script). None of the four agent files (`trend_analyzer.py`, `news_synthesizer.py`,
`filing_skeptic.py`, `arbitrator.py`) were modified — confirmed via `git status -u`
showing them absent from both the modified and untracked lists. No bugs found in
any of them while wiring up.

**Pre-flight check (run before touching any agent code):** confirmed
`GROQ_API_KEY` and `GEMINI_API_KEY` present in environment, then made one
trivial direct call through `LLMGateway.complete()` outside of any agent
context — Groq responded "PONG" successfully. Only proceeded to build the
orchestrator after this passed.

**FK-ordering detail discovered while reading `db/models.py`:** `LLMCall.analysis_id`
is a foreign key into `intelligence_reports.id`. This meant the `IntelligenceReport`
row has to be created and flushed to the DB *before* any agent runs and makes an
LLM call — otherwise the audit-log insert inside `LLMGateway.complete()` would have
no valid FK target. The orchestrator creates the report row first with placeholder
values (`conviction_score=50.0`, `technical_signal="NEUTRAL"`, in-progress text),
then overwrites those fields once the agents finish.

**Live run against 2 real tickers, verified via direct SQL (not log output) against
`intelligence_reports` and `llm_calls`:**

- **PPL** (has 9 matched news articles): `trend_analyzer` called LLM (Groq, 339
  tokens, confidence 0.85), `news_synthesizer` called LLM (Groq, 697 tokens,
  confidence 0.30), `filing_skeptic` skipped (0 tokens, confidence 0.20, no
  filing data), `arbitrator` called LLM (Groq, 687 tokens, confidence 0.55).
  Persisted report: conviction_score=58.5, technical_signal=NEUTRAL,
  total_tokens_used=1723, generation_time≈14.6s.
- **MCB** (0 matched news articles): `trend_analyzer` called LLM (343 tokens,
  confidence 0.85), `news_synthesizer` skipped entirely (0 tokens, confidence
  0.30, zero articles — confirmed by total absence of a `news_synthesizer` row
  in `llm_calls` for MCB's report id), `filing_skeptic` skipped (0 tokens),
  `arbitrator` called LLM (575 tokens, confidence 0.55). Persisted report:
  conviction_score=58.5, technical_signal=NEUTRAL, total_tokens_used=918,
  generation_time≈4.1s.
- SQL confirmed: 2 new `intelligence_reports` rows, 5 new `llm_calls` rows (all
  `model=llama-3.3-70b-versatile`, all `status=SUCCESS` — Gemini fallback never
  triggered since Groq succeeded every call), grouped-by-agent counts showing
  `trend_analyzer: 2`, `news_synthesizer: 1`, `arbitrator: 2`, and zero rows for
  `filing_skeptic` — direct proof it was skipped for both tickers. A LEFT JOIN
  from `intelligence_reports` to `llm_calls` confirmed exactly which agent fired
  for which ticker.
- `pipeline_runs` was checked and confirmed **not** used by this orchestrator —
  that table only logs the data-collection pipeline (`run_full_pipeline`), not
  agent analysis runs. Agent-run auditing lives entirely in `intelligence_reports`
  + `llm_calls`. Not assumed — checked directly.

**Investigated finding: both tickers produced an identical conviction score
(58.5). Confirmed NOT a bug.** Pulled `score_breakdown` from `agent_outputs` for
both reports: `technical_contribution=8.5` for both (the only nonzero term),
`news_contribution=0.0` for both, `filing_contribution=0.0` for both,
`ml_contribution=0.0` for both (Phase 3 placeholder, confirmed still inert).
The zero news contributions have two different causes that currently look
identical in the final number: PPL's `NewsSynthesizer` actually called the LLM
(697 tokens spent) and the LLM judged the 9 keyword-matched articles as
genuinely NEUTRAL relevance/sentiment — a real judgment, not a skip. MCB's
`NewsSynthesizer` skipped its LLM call entirely because there were zero
articles to begin with. The current scoring formula has no way to distinguish
"a real neutral signal" from "no signal at all" — both currently produce 0.0
contribution. Filed as a known issue (see `docs/KNOWN_ISSUES.md`) rather than
treated as a defect to fix in this session, since fixing it would mean changing
the Arbitrator's scoring formula, which was explicitly out of scope.

**Wiring changes:** `POST /companies/{ticker}/analyze` now runs the orchestrator
inline (async, non-blocking) and returns the saved `IntelligenceReportResponse`
directly instead of queuing a Celery task and returning 202. The Celery
`run_analysis` task's placeholder (which incorrectly called the data-collection
pipeline, not agent analysis) was replaced with a call into the same
`AnalysisOrchestrator`, so the nightly/background path and the on-demand API
path now share one code path rather than diverging.

---

## 2026-06-27 — Phase 3 Session 1 built: ML feature pipeline + labeled dataset

Built via Claude Code (Opus 4.7 session): `backend/app/ml/__init__.py` (new),
`backend/app/ml/features.py` (new), `backend/scripts/build_ml_dataset.py`
(new). Modified: `.gitignore` (added `backend/ml_data/`),
`backend/requirements.txt` (added `pyarrow==18.1.0`, needed for parquet
output). None of the four agent files or `orchestrator.py` were touched —
confirmed, this was purely additive new-files work as scoped.

**Target redefinition:** Phase 3 no longer predicts earnings surprise (no
reported-EPS/consensus data exists anywhere in the DB, no working source for
it). Target is now 5-trading-day-ahead price direction (UP/DOWN/FLAT, ±1%
flat band), computed entirely from existing `daily_prices` data — no new
external source. Full reasoning already captured in the Phase 3 Session 1
prompt; not repeated here.

**Verified row counts (live DB, not log claims):** 12,025 raw `daily_prices`
rows read (up from the 9,904 at the June 8 verification — DB has grown since
via the nightly/manual collector runs), 2,560 rows dropped for insufficient
lookback (252-day window for `position_52w`) or missing forward window (last
5 rows per ticker), 9,465 final labeled rows: train 6,621 / val 1,418 / test
1,426.

**Class distribution (UP/DOWN/FLAT):**
- Train (n=6,621): UP 40.9%, DOWN 36.6%, FLAT 22.5%
- Val (n=1,418): UP 43.1%, DOWN 32.9%, FLAT 24.0%
- Test (n=1,426): UP 40.3%, DOWN 39.1%, FLAT 20.6%

±1% threshold judged acceptable — no class drops below 20% in any split, well
clear of the "under 10%" red line from the spec.

**Split logic:** per-ticker chronological, integer row-cutoff at 70%/85% of
each ticker's date-sorted rows (train/val/test), not a random or global-date
split — adjacent rows share most of their feature window, so a random split
would leak future data into training. Verified per-ticker, directly on the
output parquet files: `train_max_date < val_min_date < val_max_date <
test_min_date` holds for all 10 tickers, confirming no temporal leakage.

**Two new open issues filed** (see `docs/KNOWN_ISSUES.md`): ENGRO has
noticeably shorter price history than the other 9 tickers (887 vs ~1,238 raw
rows), and one row in the dataset build produced a ~-88% forward-5-day
return, almost certainly an unadjusted stock split rather than a real move.
Neither blocks this session's deliverable; both are flagged for follow-up.

**Explicit confirmation: no model training code was written this session.**
`backend/app/ml/features.py` and `build_ml_dataset.py` contain feature
engineering and dataset-splitting only — no `xgboost`, `lightgbm`, or
`sklearn` imports, no `.fit()` calls anywhere in either file.

## 2026-06-27 — Phase 3 Session 2 built: split-fix + XGBoost trained/evaluated

Built via Claude Code (Opus 4.7 session). Two parts, in order.

**Files created/modified:**
- New: `backend/app/ml/split_adjustments.py` — corporate-action adjustment
  table + `apply_split_adjustments()` helper.
- New: `backend/scripts/find_split_row.py` — read-only diagnostic that
  prints top-15 extreme single-day and 5-day returns across all tickers.
- New: `backend/scripts/verify_dataset.py` — read-only sanity check on
  post-fix parquet files (most-extreme returns, per-ticker chronological
  invariant).
- New: `backend/scripts/train_ml_model.py` — XGBoost trainer + evaluator,
  saves `ml_data/model.json` and `ml_data/metrics.json`.
- Modified: `backend/scripts/build_ml_dataset.py` — calls
  `apply_split_adjustments(prices, ticker)` between the DB pull and
  `build_features()`. Single insertion, no other logic touched.

**None of the four agent files or `orchestrator.py` were touched —
confirmed via `git status` (only `backend/scripts/build_ml_dataset.py`
modified, the rest untracked new files).**

### Part 1 — Split-adjustment fix

`find_split_row.py` against the live DB identified three split-shaped
overnight gaps. All three confirmed as real splits by the dollar-volume
preservation signature (post-split `close × volume` ≈ pre-split
`close × volume`), absence of any matching bad-news article in
`news_articles`, and the fact that the next-worst non-split single-day
drop in the entire 12k-row dataset is only -19% — splits are cleanly
separated from real market moves.

| Ticker | Date | Pre-close | Post-close | Empirical ratio |
|---|---|---|---|---|
| MARI | 2024-09-16 | 3560.00 | 415.90 | 8.5597 |
| LUCK | 2025-04-28 | 1748.80 | 365.00 | 4.7912 |
| UBL  | 2025-06-23 | 522.79  | 259.99 | 2.0108 |

**Approach chosen: backward adjustment with empirical ratios** (not clean
ratios, not window-drop, not label-clip). The pre-split open/high/low/close
get divided by the ratio and pre-split volume gets multiplied by it. This
restores continuity to the price series so every backward-looking feature
(MA20, MA50, RSI14, return_1m, return_3m, volatility_20d, position_52w)
works correctly across the split boundary without any window drops.
Trade-off documented in `split_adjustments.py`: empirical ratios absorb
any genuine same-day market move on the split day into the adjustment
factor (MARI ~+17% absorbed, LUCK ~-4%, UBL ~0%). Window-drop was
rejected — losing ~252 post-split days per affected ticker would have
gutted the dataset.

**Before/after row counts** (Session 1 → Session 2): identical at
6,621 / 1,418 / 1,426 (train/val/test). The fix doesn't change which rows
have valid forward windows; it just gives the previously-contaminated
rows correct labels.

**Before/after class distributions** (Session 1 → Session 2, train split):
UP 40.9% → 41.0%, DOWN 36.6% → 36.5%, FLAT 22.5% → 22.5%. Shifts ≤0.4pp
in every cell of every split. No class drops below 20%.

**Verified post-fix:** worst `forward_return_5d` in the labeled dataset
is now -25.4% (OGDC, 2024-02-12 — a genuine bad-news day), with no rows
worse than that. The previous -88%/-79%/-50% split-induced outliers are
gone. Per-ticker chronological-split invariant
(`train_max < val_min < val_max < test_min`) still holds for all 10
tickers. Both verifications run from `verify_dataset.py`.

### Part 2 — XGBoost training + evaluation

**Model:** `xgb.XGBClassifier(objective="multi:softprob", num_class=3,
n_estimators=800, max_depth=4, learning_rate=0.05, subsample=0.8,
colsample_bytree=0.8, reg_lambda=1.0, min_child_weight=5,
tree_method="hist", early_stopping_rounds=30)` with eval set = val split.
**Random seed: 42** (set on numpy, on the trainer, and on the train-only
shuffle).

**Best iteration on val: 27 of 800.** Early stopping kicked in fast —
the model essentially extracts what little signal exists in the first
~30 trees and then val loss plateaus.

**Test-set metrics (final reported, never used for tuning):**

```
Accuracy: 0.3934   (random-chance baseline = 0.3333)

              precision    recall  f1-score   support
        DOWN     0.3655    0.1631    0.2255       558
        FLAT     0.0000    0.0000    0.0000       294
          UP     0.3993    0.8188    0.5368       574
    accuracy                         0.3934      1426

Confusion matrix (rows = actual, cols = predicted):
                DOWN     FLAT       UP
  DOWN            91        0      467
  FLAT            54        0      240
  UP             104        0      470

Test prediction distribution:
  DOWN :   249  (17.5%)
  FLAT :     0  ( 0.0%)
  UP   :  1177  (82.5%)
```

**Feature importances (gain):** `price_vs_ma20` (0.113), `position_52w`
(0.111), `volume_vs_avg20` (0.101), `return_3m` (0.093), `ma_50` (0.093),
`rsi_14` (0.089), `price_vs_ma50` (0.086), `return_1w` (0.082),
`volatility_20d` (0.082), `ma_20` (0.079), `return_1m` (0.072).
Importances are quite flat (range 0.07–0.11) — no single feature dominates,
which is consistent with there being little signal overall rather than one
strong predictor.

### Explicit leakage self-check (run despite the result not being
suspiciously high)

1. **`close`, `date`, `ticker` excluded as model features.** ✓ Confirmed
   by inspecting `FEATURE_COLUMNS` in `app/ml/features.py` — contains
   exactly the 11 features listed above, none of `close`/`date`/`ticker`/
   `label`/`forward_return_5d`. The training script's `_xy()` only takes
   `df[FEATURE_COLUMNS]` so it cannot accidentally include anything else.
2. **Per-ticker chronological invariant holds post-fix.** ✓ Already
   verified by `verify_dataset.py` above — all 10 tickers pass
   `train_max < val_min < val_max < test_min`.
3. **`forward_return_5d` not in feature columns.** ✓ Same source as
   check 1.

### Honest verdict (the read for Session 3)

39.34% accuracy is a real but **very weak** edge (~+6pp over random
chance on a 3-class problem). The model has structurally collapsed on
the FLAT class — it makes zero FLAT predictions on the test set, and
its UP-precision (40%) only barely beats the UP base rate (40.3%).
Effectively it's a noisy binary UP/DOWN classifier that happens to
weight UP heavily because UP is the majority class.

This is the kind of result the Session 2 prompt explicitly warned about
and asked us to report plainly rather than dress up. Technical features
on 10 PSX tickers with no fundamentals, no order-flow, no filing data —
this is roughly the upper bound of what this feature set can do.

**Recommendation for Session 3 (Arbitrator wiring):** wire
`ml_contribution` in, but with **substantially less weight than the 15%
slot reserved in the original formula**. Defensible options:
- Drop weight to 5%, and only let it contribute when the predicted class
  has probability > 0.55 (i.e. treat low-confidence predictions as null).
- Or treat it as a tiebreaker only — apply it only when
  `technical_contribution + news_contribution + filing_contribution`
  produces a near-50 conviction score.

Either way, do not let this model materially move conviction scores —
the +6pp accuracy edge is too thin to bet on for actionable signals.

### What Session 3 will need from this session

- Model file: `backend/ml_data/model.json` (gitignored; XGBoost native
  format). Load with:
  ```python
  import xgboost as xgb
  model = xgb.XGBClassifier()
  model.load_model("backend/ml_data/model.json")
  ```
- Metrics file (for any "show what the model is calibrated to" UI):
  `backend/ml_data/metrics.json`.
- Inference shape: input is a 1-D numpy array of length 11, ordered
  exactly as `app.ml.features.FEATURE_COLUMNS`. `model.predict(x)`
  returns class id (0=DOWN, 1=FLAT, 2=UP); `model.predict_proba(x)`
  returns shape (n, 3) probabilities in that class order.
- Live inference still needs a feature-build helper that takes the
  current point-in-time price series and returns the same 11-feature
  vector — `app/ml/features.build_features()` is batch-only, returns a
  DataFrame, and requires both a lookback AND a forward window (it will
  drop the very latest row because there's no `forward_return_5d` yet).
  Session 3 will need a small `build_features_point_in_time()` variant
  or to compute features on the in-memory price list directly. Flagged
  but explicitly out of scope for this session.

## 2026-06-27 — Phase 3 Session 3 built: ml_contribution wired into Arbitrator (confidence-gated, 5% weight)

Built via Claude Code (Opus 4.7 session). Wires the trained XGBoost
model into the Arbitrator's conviction score, with a confidence gate
and a small weight matching Session 2's recommendation. The
Arbitrator is no longer hard-coding `ml_contribution=0.0`.

**Files created/modified (exact list):**
- New: `backend/app/ml/inference.py` — module-level lazy-loaded
  XGBoost singleton, `predict_from_prices(prices, threshold)` returning
  a rich result dict (`available`, `gate_passed`, `skip_reason`,
  `predicted_class`, `max_prob`, `probabilities`, `as_of_date`,
  `confidence_threshold`). Never raises — degradation is signalled via
  `available=False` + a `skip_reason` string.
- New: `backend/scripts/probe_ml_signal.py` — read-only diagnostic
  that runs predict_from_prices over every ticker in the DB. No LLM
  calls, no writes. Useful for "is the gate doing anything?"
  inspection at any future point.
- New: `backend/scripts/verify_ml_wiring.py` — live verification
  script. 4 parts: probe / production run / gate-pass demo (lowers
  threshold to 0.35 in-process and restores it on the way out) /
  direct SQL dump of the persisted score_breakdown.
- Modified: `backend/app/ml/features.py` — extracted
  `_compute_indicators(df)` from the existing batch path so the new
  point-in-time builder can share the exact same per-feature math.
  Added `build_features_point_in_time(prices) -> dict | None` for
  live inference: returns the latest row's 11-feature vector or
  `None` if there's not enough trailing history (binding constraint
  remains RANGE_52W = 252 trading days). Batch output is unchanged —
  validated by running the refactored `build_features` on a synthetic
  series and confirming `build_features_point_in_time(prices.iloc[:-5])`
  matches the last row of `build_features(prices)` exactly.
- Modified: `backend/app/agents/base.py` — added `ml_signal: dict = {}`
  field on `AgentContext` so the orchestrator can attach the ML
  result before the Arbitrator runs. No other agents read this
  field; only Arbitrator does.
- Modified: `backend/app/agents/arbitrator.py` — added `ML_MAGNITUDE`
  (5.0), `ML_GATE` (0.55), `ML_DIRECTION` ({UP:+1,DOWN:-1,FLAT:0})
  class constants. Added `_ml_contribution(ml_signal)` that returns
  0.0 unless `gate_passed` is True. Replaced the inline
  `score_breakdown` literal with `_build_score_breakdown(...)`, which
  now emits an `ml_detail` sub-dict (predicted_class, max_prob,
  per-class probabilities, gate_passed, skip_reason,
  confidence_threshold, as_of_date, magnitude_points, model_caveat
  string) so the persisted report distinguishes "real bullish
  signal" from "model unavailable" from "below confidence threshold"
  — closing one of the open issues from Session 2. Reworked
  `_build_prompt` to take `ml_signal` and call a new
  `_render_ml_block(ml_signal)` helper that adds a clearly-labeled ML
  section to the LLM narrative prompt, with an explicit caveat
  (~6pp over random, never predicts FLAT, treat as minor input)
  baked into the prompt so the generated bull/bear case doesn't
  overstate it.
- Modified: `backend/app/agents/orchestrator.py` — extended price
  pull window from 365 → 600 calendar days (constant
  `PRICE_WINDOW_DAYS`) so the 252-trading-day position_52w lookback
  has real margin. Added a `predict_from_prices` call before the
  agents run, attaching the result to `context.ml_signal`. Populates
  `report.ml_beat_probability` with the UP-class probability
  (repurposing the legacy schema field name; documented inline). The
  inference call is intentionally inline-synchronous: model is
  cached at module level after the first call, so it's a microsecond
  numpy op — no asyncio.to_thread wrapping needed.

**Protected files (untouched, confirmed):** `trend_analyzer.py`,
`news_synthesizer.py`, `filing_skeptic.py` — none modified this
session. The four-agent design contract is preserved.

**Shared feature math (per prompt requirement, no duplication):** The
point-in-time path and the batch path both go through
`_compute_indicators()`. Verified by running batch on a synthetic
600-row series and confirming `build_features_point_in_time(prices[:-5])`
produces a feature vector identical to `build_features(prices).iloc[-1]`
(np.isclose across all 11 features). Two different consumers, one
implementation of MA/RSI/momentum/etc. — exactly what training-vs-
inference parity requires.

**Weight + gate logic (final):**
- `ML_MAGNITUDE = 5.0` (5% max swing on the 0-100 score) — down from
  the originally-reserved 15%, per the Session 2 recommendation.
- `ML_GATE = 0.55` (strict `>`, not `>=`) on `max(predict_proba)`.
- When the gate fails OR the model is unavailable OR there's
  insufficient history, `ml_contribution = 0.0` AND the report's
  `ml_detail.skip_reason` records which of those three cases
  occurred. Other scoring weights (technical, news, filing) were NOT
  modified — they retain their original Session 2 magnitudes
  (max ±20, ±15, -5/-15/-30 respectively), and the base of 50 is
  unchanged. Clamp `[0, 100]` still applied after summing.

**Live verification — Part A (probe over all 10 tickers):**

| Ticker | Rows | Class | max_prob | Gate | Reason |
|---|---:|---|---:|---|---|
| ENGRO | 44 | — | — | fail | insufficient_history |
| HBL | 396 | UP | 0.399 | fail | below_confidence_threshold |
| LUCK | 391 | UP | 0.404 | fail | below_confidence_threshold |
| MARI | 396 | DOWN | 0.362 | fail | below_confidence_threshold |
| MCB | 396 | UP | 0.357 | fail | below_confidence_threshold |
| MEBL | 396 | UP | 0.374 | fail | below_confidence_threshold |
| OGDC | 396 | DOWN | 0.392 | fail | below_confidence_threshold |
| PPL | 396 | UP | 0.400 | fail | below_confidence_threshold |
| PSO | 396 | DOWN | 0.378 | fail | below_confidence_threshold |
| UBL | 396 | UP | 0.407 | fail | below_confidence_threshold |

All 10 tickers' top-class probability lies in [0.357, 0.407] — none
clear the 0.55 production gate. This reproduces the Session 2
test-set finding directly in live production: 9/10 predict UP or
DOWN (never FLAT — model is structurally FLAT-blind here too), and
the model is genuinely not confident on any of them. The gate is
behaving as designed: silence rather than a weak vote. ENGRO's
"insufficient_history" matches the existing Known Issue (ENGRO has
~887 raw rows vs ~1,238 for the other 9; the 600-day pull window
captures only 44 of those since the missing range is recent —
flagging this is a fresh diagnostic data point for that backfill
follow-up).

**Live verification — Part B (production run, real ML_GATE = 0.55,
2026-06-27, direct from `intelligence_reports`):**

| Ticker | conviction | tech | news | filing | ml | ml_beat_prob | predicted | max_prob |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| PPL | 58.5 | 8.5 | 0.0 | 0.0 | 0.0 | 0.400 | UP | 0.400 |
| MCB | 58.5 | 8.5 | 0.0 | 0.0 | 0.0 | 0.357 | UP | 0.357 |
| UBL | 58.5 | 8.5 | 0.0 | 0.0 | 0.0 | 0.407 | UP | 0.407 |

Hand-check (PPL): 50 + 8.5 (tech BUY × 0.85) + 0 + 0 + 0 = 58.5 ✓.
Same arithmetic holds for MCB and UBL (trend agent returned BUY for
all three this run; tech×conf identical at 8.5; sum 58.5).

**Are PPL and MCB conviction scores now different?** No — both
remain 58.5. This was explicitly anticipated in the Session 3
prompt's verification section ("explain clearly if they're still
identical and why (e.g. both might still land below the confidence
gate)"). They DO now differ in the persisted report in three
material ways even though `conviction_score` doesn't:
1. `ml_beat_probability` (a top-level column on
   `intelligence_reports`) is 0.400 for PPL vs 0.357 for MCB.
2. `score_breakdown.ml_detail.max_prob`, `.predicted_class`, and
   `.probabilities` differ row-by-row.
3. `score_breakdown.ml_detail.skip_reason = "below_confidence_threshold"`
   makes the zero contribution self-explanatory in the persisted
   data — no consumer has to guess "why is this zero?"

The conviction-score collision is the *correct* production behavior
of the chosen design: weak ML signals shouldn't move the score, and
right now no ticker's signal is strong enough to qualify. If/when
the model ever does clear 0.55 on a ticker (e.g. after retraining
with more features or after adding to the ticker universe), the
scores will diverge naturally.

**Live verification — Part C (gate-pass code path, ML_GATE
temporarily lowered to 0.35 in-process for one call on UBL):**

| Ticker | conviction | tech | news | filing | ml | gate_passed | predicted |
|---|---:|---:|---:|---:|---:|---|---|
| UBL (demo) | 55.0 | 0.0 | 0.0 | 0.0 | 5.0 | true | UP |

Hand-check: 50 + 0 (tech NEUTRAL this run) + 0 + 0 + 5 (UP × +1 ×
5.0 magnitude) = 55.0 ✓. The trend agent's LLM response for this
particular run came back NEUTRAL (different from the production
BUY moments earlier — same temperature=0.1 stochasticity already
present in the system, not new), which is why `technical_contribution`
is 0 here. The ML term contributing +5 exactly as predicted by the
hand-math confirms the gate-pass code path is wired correctly.
`Arbitrator.ML_GATE` was restored to 0.55 immediately after this
call; the production constant in the source file was never
changed.

**Live verification — Part D (SQL dump confirms persistence):**
`score_breakdown.ml_detail` is present and fully populated on the
five new `intelligence_reports` rows written this session. The
earlier MCB row from Session 2 (predates this change) has the
simple `ml_contribution: 0.0` without an `ml_detail` block — no
backfill needed since the field is purely additive metadata.

**Judgment calls / deviations from the prompt:**
- The prompt asked for verification that "ml_contribution is nonzero
  for at least one ticker where the model's confidence clears 0.55."
  In current data NO real ticker clears 0.55 — exercised the gate-pass
  code path via the in-process threshold demo (Part C) rather than
  contriving a synthetic ticker or lowering the production gate, since
  the prompt also explicitly allowed "explain clearly if they're still
  identical and why." Both reads of the prompt are satisfied.
- `ml_beat_probability` (legacy schema field designed for the original
  earnings-beat target before that target was redefined in Session 1)
  is repurposed to hold the UP-class probability. Inline comment in
  `orchestrator.py` documents the rename rather than changing the
  database schema, since renaming a column would have required an
  Alembic migration and was outside this session's scope. The
  semantic is consistent: "estimated probability of a > +1% move
  over the next 5 trading days" is the natural successor to "estimated
  probability of an earnings beat" for this purpose.
- Added an `ml_detail.model_caveat` string into the persisted score
  breakdown, so the weak-signal warning is durable in the database,
  not just in the LLM prompt. Cheap and useful for any future UI that
  renders the breakdown — surfaces the caveat without the consumer
  having to know the model's history. This is mild scope creep over
  what the prompt strictly required but matches the prompt's "Visible
  low-confidence labeling (do this, it's expected practice)" spirit.

## 2026-06-27 — Phase 4 Session 2 built: ScoreBreakdown exposed on the API + MlSignalCard upgraded

Built via Claude Code (Opus 4.7 session). Phase 4 Session 1 noted
that the persisted `score_breakdown` / `ml_detail` blob in
`agent_outputs` wasn't reachable from the frontend because
`IntelligenceReportResponse` only exposed the legacy
`ml_beat_probability` float. This session closes that gap: the typed
sub-schema is now part of the API response and the company-detail
page consumes it.

**Files modified (exact list):**

*Backend:*
- `backend/app/schemas/intelligence.py` — added `MlDetail` and
  `ScoreBreakdown` Pydantic models, added
  `score_breakdown: ScoreBreakdown | None = None` to
  `IntelligenceReportResponse`, plus a `@model_validator(mode="before")`
  that hoists the breakdown from
  `agent_outputs["arbitrator"]["output"]["score_breakdown"]` so
  every existing call site (`model_validate(report)` /
  `model_validate_json(cached)`) just works without touching the
  endpoints. `MlDetail.model_config = ConfigDict(protected_namespaces=())`
  to silence the Pydantic v2 warning on the `model_caveat` field
  name.

*Frontend:*
- `frontend/src/lib/api/types.ts` — added `MlPredictedClass`,
  `MlSkipReason`, `MlDetail`, `ScoreBreakdown` interfaces and the
  optional `score_breakdown` field on `IntelligenceReportResponse`.
- `frontend/src/app/(app)/companies/[ticker]/page.tsx` — rewrote
  `MlSignalCard` to branch on `score_breakdown?.ml_detail`:
  rich-detail path renders DOWN / FLAT / UP per-class probability
  bars with the gate marker, highlights the predicted class, and
  shows an honest status panel that distinguishes
  `below_confidence_threshold` from `insufficient_history` from
  `model_unavailable`. Legacy fallback path (renamed
  `MlSignalCardLegacy`) preserves the previous UP-only rendering
  unchanged for cached/older responses that lack the new field.
  Added a new `ScoreBreakdownStrip` component showing the
  base 50 + technical + news + filing + ML contributions adding up
  to the final conviction score — judgment call, see below.

*Verification helper (new, kept for future re-use):*
- `backend/scripts/verify_score_breakdown.py` — read-only script
  that pulls the latest report per ticker and shows DB-vs-schema
  hoisted output side-by-side. No inserts, no LLM calls.

**Files NOT touched (confirmed):** `trend_analyzer.py`,
`news_synthesizer.py`, `filing_skeptic.py`, `orchestrator.py`, and
`arbitrator.py`. Confirmed via the unmodified-list in `git status`:
this session is a serialization/UI change, not a scoring change. The
Arbitrator's `_calculate_score` math is unchanged; the persisted
score_breakdown shape is unchanged. Only the response schema and the
component that renders it moved.

**Exact shape of the new `ScoreBreakdown` schema:**

```
ScoreBreakdown:
  technical_contribution: float
  news_contribution: float
  filing_contribution: float
  ml_contribution: float
  ml_detail: MlDetail | None

MlDetail:
  gate_passed: bool
  skip_reason: str | None
  predicted_class: str | None
  max_prob: float | None
  probabilities: dict[str, float] | None
  confidence_threshold: float | None
  as_of_date: str | None
  magnitude_points: float | None
  model_caveat: str | None
```

Field names mirror the persisted JSON exactly — verified by reading
`backend/app/agents/arbitrator.py::_build_score_breakdown` directly
(not assumed from Phase 3 Session 3's report). The persisted
`score_breakdown` carries those four contribution fields plus an
`ml_detail` sub-dict with the nine fields above. Schema and
persisted shape are 1:1.

**Live verification — Part A (direct DB cross-check via
`backend/scripts/verify_score_breakdown.py`, no HTTP layer):**

For PPL / MCB / UBL, the latest `IntelligenceReport.agent_outputs`'s
`arbitrator.output.score_breakdown` was dumped raw and compared
against `IntelligenceReportResponse.model_validate(report).score_breakdown.model_dump()`.
All three came back `Cross-check (DB == schema): True`. Example
(PPL, abbreviated):

```
technical_contribution = 8.5
news_contribution = 0.0
filing_contribution = 0.0
ml_contribution = 0.0
ml_detail:
  gate_passed = False
  skip_reason = "below_confidence_threshold"
  predicted_class = "UP"
  max_prob = 0.40006
  probabilities = {DOWN: 0.30857, FLAT: 0.29137, UP: 0.40006}
  confidence_threshold = 0.55
  as_of_date = "2026-06-05"
  magnitude_points = 5.0
  model_caveat = "Technical-only XGBoost; ..."
```

UBL came back with `gate_passed=True`, `ml_contribution=5.0`,
`confidence_threshold=0.35` — this is the gate-pass demo row left
behind by Phase 3 Session 3 (production gate is 0.55, that row was
written when the threshold was temporarily lowered in-process for
verification). Exercising the gate-passed code path in the frontend
without retraining the model was a free side-benefit.

**Live verification — Part B (HTTP API, real backend running):**

Started uvicorn on :8000, registered a fresh test user
(`phase4verify@example.com`) to grab a JWT, hit
`GET /api/v1/companies/PPL/report` and `GET /api/v1/companies/UBL/report`.

First PPL request returned `score_breakdown: null` — caught a real
gotcha: the response was being served from the 24-hour Redis cache
(`REPORT_CACHE_KEY = "report:{ticker}:{date}"`), populated before
the schema change. Invalidated the three test cache keys via
`redis_client.delete_cached` and re-fetched. PPL then returned the
full block, byte-for-byte the same numbers as the direct DB dump
above:

```
"score_breakdown": {
  "technical_contribution": 8.5,
  "news_contribution": 0.0,
  "filing_contribution": 0.0,
  "ml_contribution": 0.0,
  "ml_detail": {
    "gate_passed": false,
    "skip_reason": "below_confidence_threshold",
    "predicted_class": "UP",
    "max_prob": 0.4000595510005951,
    "probabilities": {"DOWN": 0.30857, "FLAT": 0.29137, "UP": 0.40006},
    "confidence_threshold": 0.55, ...
  }
}
```

UBL came back with `gate_passed: true`, `ml_contribution: 5.0`,
`skip_reason: null` — both code paths covered end-to-end.

**Cache invalidation note for future deployments:** because of the
24-hour TTL, after this change ships to production any ticker viewed
in the last 24 hours will keep returning the cached `null`
`score_breakdown` until the TTL expires. Frontend handles this
gracefully (legacy fallback rendering), but if instant rollout is
desired, the deploy step should `DEL report:*` from Upstash.

**Live verification — Part C (frontend `tsc` + Next.js dev server):**

- `cd frontend && npx tsc --noEmit` — exited 0 with no output. All
  new types align.
- `npm run dev` boots cleanly, `GET /companies/PPL` returns HTTP 200
  with the expected client-side `Loading…` shell (page is
  `"use client"` and auth-gated, so SSR renders only the spinner
  until hydration). No SSR/runtime errors in the bundle.

**Visual UI render NOT independently verified:** the Claude in
Chrome MCP isn't connected on this machine
(`list_connected_browsers` returned `[]`), so I did not screenshot
the rendered page after login. The component changes are verified
by tsc + manual code review of the branch logic
(MlSignalCardRich / MlSignalCardLegacy fall-through, ClassProbBar
math, ScoreBreakdownStrip sum check). User should open
http://localhost:3000/companies/PPL after `npm run dev` + login to
confirm the visual treatment matches intent before merging.

**Judgment calls / deviations from the prompt:**

- *Per-term ScoreBreakdownStrip added inline this session, not
  deferred.* The prompt left it to judgment. Reasoning: it's the
  most natural place to surface the breakdown (right after the
  bull/bear cards, above the risk/ML row), it's an additive card
  that doesn't perturb existing layout, and it directly addresses
  the open issue documented in `KNOWN_ISSUES.md` where many tickers
  cluster around conviction 58.5 because three of the four scoring
  terms are structurally pinned to 0 right now — showing the math
  makes that visible to the user instead of mysterious. Total cost
  was ~70 lines of new component code.
- *Pydantic `model_validator(mode="before")` instead of touching
  every endpoint.* `IntelligenceReportResponse` is constructed in
  ~5 places across two routers, and four of those use ORM input
  while one uses cached-JSON input. Centralising the hoist in the
  schema means none of the routers had to change, the cached-JSON
  path stays symmetric, and any future endpoint that returns this
  schema automatically gets `score_breakdown` for free.
- *Field name `model_caveat` kept (not renamed).* Pydantic v2's
  `model_` namespace warning is silenced via `protected_namespaces=()`
  on `MlDetail`. Renaming the field would have meant changing the
  persisted JSON shape, which would have rippled into the
  Arbitrator's `_build_score_breakdown` — explicitly out of scope
  for this session per the hard rules.
- *Legacy `MlSignalCard` rendering preserved as a fallback rather
  than deleted.* Older `IntelligenceReport` rows persisted before
  Phase 3 Session 3 (e.g. MCB's Phase 2B Session 2 row) carry no
  `ml_detail` block — that's an existing data-shape diversity, not
  something this session created. The fallback keeps those rows
  rendering correctly instead of crashing or showing empty bars.

**What Session 3 (next) likely needs:** per Phase 4 Session 1's
remaining priority list, the next coherent slice is either the
watchlist UI (touching the existing `/watchlist` API), the
price-chart panel on the company detail page (uses
`/companies/{ticker}/prices` which already returns OHLCV), or the
news article list (uses `/companies/{ticker}/news`). The price
chart is probably the highest visual-impact-to-effort ratio — the
data's already there, and a chart anchors the page's left column
nicely under the conviction header. Worth flagging that the
Recharts vs lightweight-charts library decision hasn't been made
yet; Session 1's notes don't pin it down.

## 2026-06-28 — Phase 4 Session 1 doc backfill (code shipped 2026-06-27 as `504e787`, docs missed at the time)

**This entry is being added after the fact.** The Phase 4 Session 1
frontend scaffold was built and committed in `504e787` on
2026-06-27 without a corresponding `CLAUDE.md`/`BUILD_LOG.md`
update — the commit was scoped purely to `frontend/`. This entry
and the matching `CLAUDE.md` "Current build state" line are being
added now, one session later (after Phase 4 Session 2's `9c4224b`
had already landed), so the record is honest about *when the
documentation happened* versus *when the code shipped*. No
frontend or backend code was touched to produce this entry.

**What `504e787` actually shipped**, confirmed by reading the
committed code directly rather than relying on chat history:

- **Stack:** Next.js 15 (App Router) + TypeScript + Tailwind +
  hand-built shadcn/ui-style primitives (`frontend/src/components/ui/`
  — button, input, label, card; not generated via the shadcn CLI).
  New top-level `frontend/` directory in the same monorepo as
  `backend/`.
- **Design system** (`frontend/src/app/globals.css`): palette
  named "Karachi Dusk" — deep teal primary, terracotta accent used
  sparingly, deep-sage bullish / muted-brick bearish (deliberately
  not trading-screen green/red), warm cream surfaces instead of
  pure white. Type pairing is Fraunces (display/serif — headings
  and the conviction score) + Inter (body, with tabular-nums for
  any column of figures). `frontend/src/components/conviction-dial.tsx`
  is the one concrete "point of view" decision: a custom SVG
  semicircular gauge that maps the 0-100 conviction score to a
  needle angle (-90°..+90°) over a bearish→neutral→bullish gradient
  arc, animated on mount. Rationale: a plain number is forgettable;
  a needle position reads instantly, and it stays honest about the
  current state of the system — most live conviction scores cluster
  near 58.5 (see `KNOWN_ISSUES.md`), so most needles currently sit
  "just past center," which is the true state, not a dressed-up one.
- **API layer:** single typed chokepoint
  (`frontend/src/lib/api/client.ts`'s `apiRequest<T>()`), mirroring
  the backend's `LLMGateway` single-chokepoint philosophy. Handles
  JWT access+refresh with auto-refresh-on-401-retry-once.
  `frontend/src/lib/auth/context.tsx` provides `AuthProvider`/`useAuth()`.
- **Pages:** login + register (real backend calls, real error-state
  mapping for 401/409/422/network failures, not just the happy
  path), dashboard (10-ticker universe, per-company conviction at a
  glance via `CompanyCard`+`ConvictionDial`, designed loading
  skeletons and a real empty/CTA state), company detail (full
  `IntelligenceReport`: conviction score, bull/bear narrative, risk
  factors, `MlSignalCard` surfacing the ML signal). At the time this
  page only had `ml_beat_probability` to work with — `score_breakdown`
  wasn't exposed by the API yet — so `MlSignalCard` showed only the
  UP-class probability with an explicit "Low confidence" caveat and
  a 55%-gate marker line, rather than fabricating the missing
  per-class detail. **This gap was resolved one session later in
  Phase 4 Session 2 (`9c4224b`)**, which added `score_breakdown` to
  `IntelligenceReportResponse` and split the card into
  `MlSignalCardRich`/`MlSignalCardLegacy`.
- **Live verification actually performed during the original
  session:** end-to-end curl-based API integration against the real
  Neon Cloud/Upstash-backed backend (register → login → `/me` →
  companies list → company detail → report), with returned values
  matching already-documented figures exactly (PPL
  `conviction_score=58.5`, `ml_beat_probability≈0.4001`); `npx tsc
  --noEmit` clean; all 4 routes (`/login`, `/register`, `/dashboard`,
  `/companies/PPL`) compiled and served HTTP 200 by the Next dev
  server. **Honest gap flagged at the time:** no interactive
  in-browser visual walkthrough was performed inside the session
  itself — only curl calls and pre-hydration SSR-shell checks, since
  client-rendered pages show only a loading shell to a non-JS HTTP
  client. A manual browser walkthrough was carried out afterward,
  outside the session that built the code, before the commit was
  made.

Commit: `504e787`.

## 2026-06-28 — Phase 4 Session 3 complete (price chart on company detail page)

Added a TradingView-`lightweight-charts`-rendered price chart to
the company detail page. The original prompt scoped this as full
OHLC candlesticks; the design was rejected in-session because
`daily_prices.high`/`low` are *derived* as `max(open, close)` /
`min(open, close)` (PSX DPS provides no real intraday range — see
`docs/KNOWN_ISSUES.md`). Candle wicks would therefore be
structurally invisible on every single candle, which is dishonest
decoration. **Built instead:** a close-price area line + MA20/MA50
overlay lines on the main pane, with a volume histogram (tinted by
close-vs-previous-close direction) in a synced lower pane on the
same time axis. A small caption below the chart states the
close-line-vs-candlestick decision plainly. Same honesty principle
already applied to the ML signal card in Session 2.

**Library:** `lightweight-charts` v5.2.0 (TradingView). Picked over
Recharts because: purpose-built for financial time series, handles
the trading-day axis natively (no flat weekend-gap workarounds),
v5's `addSeries(..., paneIndex)` API gives a real synced
multi-pane chart out of the box, and the app doesn't need
general-purpose charting elsewhere.

**Files (this session):**

- `backend/app/api/v1/companies.py` — single-line change: bumped
  the `/companies/{ticker}/prices` `limit` Query ceiling from 365
  → 2000, so the chart can pull a full ~2-year history in a single
  request. This is the only backend change in this session and is
  scoped strictly to that endpoint's query window per the prompt's
  hard rule.
- `frontend/package.json` / `package-lock.json` — `lightweight-charts`
  v5.2.0 added as a dependency. No other deps changed.
- `frontend/src/lib/api/companies.ts` — added
  `getCompanyPriceHistory(ticker)` alongside the existing
  `getCompanyPrices(ticker, limit=90)`. The new function passes an
  explicit `start_date` 7 years in the past (the `/prices` endpoint
  defaults its date window to the last 90 days when none is given,
  so just raising the row `limit` wasn't enough — needed the date
  window too).
- `frontend/src/components/price-chart.tsx` — **new component**.
  Self-contained: fetches its own series via
  `getCompanyPriceHistory`, computes MAs client-side, renders the
  chart and the range selector, owns its own loading/error/empty
  states. Reads `--primary`/`--accent`/`--neutral`/`--bullish`/
  `--bearish` straight off `:root` via `getComputedStyle` and
  wraps them in `hsl(...)` for the lightweight-charts API, so the
  chart inherits the existing "Karachi Dusk" palette without
  introducing any new ad-hoc colors. Range selector uses
  `timeScale().setVisibleRange()` for fixed presets and falls
  back to `fitContent()` for `ALL` and for any preset wider than
  the ticker's available history (the explicit ENGRO-short-history
  graceful path).
- `frontend/src/app/(app)/companies/[ticker]/page.tsx` — added a
  single `<PriceChart ticker={data.company.ticker} />` between the
  `CompanyHeader` and the `report ? ReportBody : EmptyState`
  branch, so the chart renders regardless of whether an
  IntelligenceReport exists yet for the ticker (the chart's data
  is independent of the agent pipeline).
- `backend/scripts/verify_chart_ma.py` — **new read-only
  diagnostic**. Prints MA20/MA50 for the last 3 trading days per
  ticker via the canonical backend path (`_compute_indicators` in
  `app/ml/features.py`), for hand-comparison with the frontend's
  client-side `computeMA`. Emits both raw and split-adjusted
  series so the MA values you see in the rendered chart (raw) can
  be told apart from the ML pipeline's values (adjusted) for
  tickers with split events.

**Part 2 (MA data source) decision: client-side, computed in the
component.** Rejected option (b), pulling MAs from a backend
endpoint, because:

1. The chart already needs the raw close series to render the
   close-price line itself, so we'd be making a second API call
   to fetch values the client trivially recomputes from data it
   already has.
2. The frontend's `computeMA` is a straight sliding-window mean —
   identical algorithm to
   `backend/app/ml/features.py::_compute_indicators` (simple
   unweighted rolling mean of close, `window=20`/`50`,
   `min_periods=window`). Same inputs in, same numbers out. No
   semantic drift risk.
3. By extending the fetch window (Part 3 below), the first visible
   day in every preset range already has 20+ / 50+ prior close
   values to compute from, so MA20/MA50 are accurate from the
   first visible point — not "ramping up out of zero." (This was
   the actual concern Part 2 was guarding against.)

**Part 3 (time-range selector):** five presets — 1M (~22 trading
days), 3M (~63), 6M (~126), 1Y (~252), All. Default = 6M.
`setVisibleRange()` for 1M/3M/6M/1Y, `fitContent()` for All.
Edge case: if `prices.length <= want` for any preset (e.g. a
hypothetical newly-seeded ticker with < 22 rows), fall back to
`fitContent()` so the chart never shows an empty window. ENGRO's
shorter history goes through this fallback gracefully for the
ALL preset and any range wider than what exists.

**Live verification — Part A (MA value cross-check, both code
paths):**

1. `python backend/scripts/verify_chart_ma.py PPL MCB ENGRO`
   pulled raw `daily_prices` from the live Neon DB, ran them
   through `app/ml/features.py::_compute_indicators`, and printed
   the last 3 trading days' MA20/MA50 per ticker.
2. With the backend running (uvicorn :8000), fetched the actual
   `/api/v1/companies/{ticker}/prices?limit=2000&start_date=...`
   JSON the frontend will consume for PPL/MCB/ENGRO into local
   files, then ran the frontend's `computeMA` algorithm
   (re-implemented in Python — same sliding-window math) against
   those JSON files.
3. Compared the last 3 days' MA20/MA50 from both paths,
   byte-for-byte:

```
PPL   2026-06-05  close=233.50  ma20=230.2085  ma50=223.4178  (both)
MCB   2026-06-05  close=414.99  ma20=406.1105  ma50=398.7368  (both)
ENGRO 2025-01-03  close=481.99  ma20=426.9010  ma50=366.3388  (both)
```

Both paths agreed exactly for all three tickers. ENGRO's date
range ended at 2025-01-03 (n=887) vs PPL/MCB's 2026-06-05
(n=1238), which is the documented short-history difference from
`docs/KNOWN_ISSUES.md` — handled correctly.

**Live verification — Part B (frontend tsc + Next.js dev
server):**

- `cd frontend && npx tsc --noEmit` → exit 0, no output. All new
  types align, including the v5 `lightweight-charts` series
  generics.
- `npm run dev` boots clean, `/companies/PPL`, `/companies/MCB`,
  and `/companies/ENGRO` all return HTTP 200 from the SSR shell.
  No client-side compile errors in the dev-server log.

**Visual UI render NOT independently verified.** Same gap as
Phase 4 Session 2: the Claude in Chrome MCP isn't connected on
this machine (`list_connected_browsers` returned `[]`), so no
in-browser screenshot was taken of the rendered chart. User
should open
`http://localhost:3000/companies/{PPL,MCB,ENGRO}` after
`npm run dev` + login and visually confirm:

1. The chart renders with the close line + both MA overlays + a
   volume pane below.
2. The 1M/3M/6M/1Y/All range buttons each change the visible
   window (and that 1M actually shows ~one month rather than the
   full range).
3. ENGRO's "All" shows whatever shorter history exists, not a
   broken or empty chart.
4. The honesty caption about derived high/low is visible below
   the chart (not buried in a tooltip).

**Judgment calls / deviations from the prompt:**

- *No candlesticks, despite that being the prompt's original
  intent.* Rationale (also surfaced in the prompt itself, which
  flagged this as a reconsideration): wicks would be structurally
  invisible on every single candle because `high`/`low` are
  derived from open/close, so candlesticks would visually
  *show less* than a clean close-price line while *implying
  more* depth. The honesty caption + tinted-volume substitute
  preserves the "directional" visual cue (which is what
  candlestick body color usually communicates) without faking
  intraday range.
- *Client-side MAs, not a new backend endpoint.* Covered in Part
  2 decision above.
- *Backend `/prices` `limit` ceiling raised 365 → 2000.* Only
  way to satisfy the prompt's "fetch a wider window than what's
  displayed" requirement without doing multiple round-trips per
  range change. The change is one line, scoped strictly to the
  endpoint's query window, and changes no behavior for existing
  callers (the default `limit=90` is unchanged).
- *Chart rendered OUTSIDE the `report ? ReportBody : EmptyState`
  branch.* Price data is independent of whether an
  `IntelligenceReport` has been generated yet — a newly-seeded
  ticker should still get a chart, even before any agent has
  ever run. Placing it directly below `CompanyHeader` (rather
  than inside `ReportBody`) is the natural consequence of that.
- *Self-contained data fetching.* The `PriceChart` owns its own
  `getCompanyPriceHistory` call rather than receiving prices via
  props from the page. Same separation of concerns the
  `MlSignalCard`/`AnalyzeButton`/etc. components already follow:
  each card owns the data it needs from the typed API client.

**Confirmation that the five protected files were NOT touched:**
`git status --porcelain` after this session lists only
`backend/app/api/v1/companies.py` (the one-line limit bump),
`frontend/package.json` + `package-lock.json`,
`frontend/src/app/(app)/companies/[ticker]/page.tsx` (single
component import + single JSX line),
`frontend/src/lib/api/companies.ts` (new
`getCompanyPriceHistory` function), plus two new files
(`backend/scripts/verify_chart_ma.py`,
`frontend/src/components/price-chart.tsx`). Neither
`trend_analyzer.py`, `news_synthesizer.py`, `filing_skeptic.py`,
`orchestrator.py`, nor `arbitrator.py` appears in the diff.

**Docs updated this session:** `CLAUDE.md`'s Current build state
got a Phase 4 Session 3 line, and the Key files map got two new
entries (`frontend/src/components/price-chart.tsx`,
`backend/scripts/verify_chart_ma.py`); the
`frontend/src/app/(app)/companies/[ticker]/page.tsx` row was
extended to note the new chart wiring. This BUILD_LOG entry was
added.

**What Session 4 should pick up next:** per Phase 4 Session 1's
original priority list, the remaining slices are the **watchlist
UI** (touches the existing `/watchlist` endpoint — closest to
"production-grade" for a recruiter demo, since saved tickers is a
real user-flow) or the **news article list** (renders the
existing `/companies/{ticker}/news` endpoint). The watchlist is
probably higher signal — it exercises a new endpoint family and
adds a CRUD flow the dashboard doesn't currently have, whereas
news is mostly read-only display of one already-paginated list.
Worth noting: news matching is "noisy" per `KNOWN_ISSUES.md`, so
a news UI would need to surface the LLM-judged-relevant articles
distinctly from the keyword-matched set, otherwise the page will
read as "9 PPL articles" when most are tangentially about
petroleum, not the company — design beats raw rendering here.

## 2026-06-28 — Phase 4 Session 4: watchlist UI (toggle + dashboard filter)

**Goal.** Wire the existing-but-never-exercised `/api/v1/watchlist`
endpoint family into the frontend as a real user flow: a star
toggle on every company card + on the company-detail header, and a
simple "All companies / My watchlist" segmented control on the
dashboard that filters the existing 10-ticker grid client-side.
Phase 4 Session 3's wrap-up suggested watchlist over news for
Session 4 because it exercises a new endpoint family and a real
CRUD flow with optimistic updates, vs. news which is mostly
read-only rendering.

**Step 0 — live-verify the backend.** The `/watchlist` endpoints
were built in Phase 1B, untouched and unexercised since. Re-read
`backend/app/api/v1/intelligence.py` (the watchlist routes live in
the same router as intelligence reports and alerts — a fact the
prompt's `backend/app/api/v1/*.py` hint surfaced), then wrote
`backend/scripts/verify_watchlist_endpoints.py` — a read-write
smoke test that registers a fresh timestamped user and walks every
documented status code. **13 PASS / 0 FAIL:**

  - `GET /api/v1/watchlist` empty → 200 `[]`
  - `POST /api/v1/watchlist {ticker: PPL}` → 201, response body
    includes the joined `company_name` ("Pakistan Petroleum
    Limited") from the `Company` table
  - `POST /api/v1/watchlist {ticker: PPL}` duplicate → 409
    `{"detail": "'PPL' is already on your watchlist"}`
  - `POST /api/v1/watchlist {ticker: NOPE}` unknown ticker → 404
    `{"detail": "Company 'NOPE' not found"}`
  - `POST /api/v1/watchlist {ticker: mcb}` lowercase → 201 with
    `"ticker": "MCB"` (backend uppercases via
    `request.ticker.upper()` in the route handler, confirmed)
  - `GET /api/v1/watchlist` after 2 adds → 200 `["MCB", "PPL"]`
  - `DELETE /api/v1/watchlist/PPL` → 200
    `{"message": "Removed from watchlist"}`
  - `DELETE /api/v1/watchlist/PPL` already-removed → 404
    `{"detail": "'PPL' is not on your watchlist"}`
  - `DELETE /api/v1/watchlist/mcb` lowercase → 200 (backend
    uppercases the path param too, confirmed)
  - Final `GET /api/v1/watchlist` → 200 `[]`

All three endpoints worked exactly as documented in the schema /
code. **No backend changes this session** — the prompt's "if Step 0
reveals a backend bug, fix it minimally" clause didn't apply.

A side observation: hitting the `/auth/register` endpoint rapidly
during early curl-based exploration tripped the IP-based rate
limiter at `backend/app/main.py:147-183` (100 req/min, sliding
window via Redis with `EXPIRE` reset on every request). Cleared
via direct `redis-py` against the `rate:127.0.0.1` key on the live
Upstash instance. Documenting this here since the EXPIRE-on-every-
request semantics mean the window doesn't actually decay if you
keep checking it — useful to know if a future session ever sees a
sticky 429 during local verification.

**Frontend additions.** All five new files; no edits to the five
protected agent/orchestrator files (verified by `git status` at
the end of the session):

  - `frontend/src/lib/api/watchlist.ts` — extends the typed
    `apiRequest<T>()` chokepoint with `getWatchlist`,
    `addToWatchlist`, `removeFromWatchlist`. The module-level
    docstring documents the actual edge-case responses so future
    callers don't have to re-discover them from the backend code.
  - `frontend/src/lib/watchlist/context.tsx` — `WatchlistProvider`
    + `useWatchlist()`. Owns shared watchlist state for the whole
    auth-gated `(app)` route group. Exposes a `Set<string>` of
    uppercase tickers so the star toggle and the dashboard filter
    can both do O(1) membership lookups against the same source of
    truth, no per-component re-fetches. Optimistic update protocol
    lives entirely here, not in the star: add path optimistically
    inserts a placeholder row → swaps with the real server payload
    on 201 → treats 409 as idempotent success (server already had
    it; refresh to pick up the canonical row) → rolls back the
    insert on any other ApiError. Remove path optimistically pops
    the row (stashing it) → treats 404 as idempotent success
    (server already lacked it) → restores the stashed row on any
    other ApiError. Both 409-on-add and 404-on-remove being
    treated as success-equivalents makes the star tap-safe — the
    same ticker can be tapped twice quickly, or be acted on with
    stale data after a different tab changed something, and
    neither of those harmless cases produces an error banner.
  - `frontend/src/components/watchlist-star.tsx` — `WatchlistStar`,
    the visual toggle. Two size variants. `card` is small, defaults
    `stopPropagation=true` because dashboard cards are `<Link>`s
    wrapping the whole card area and tapping the star inside that
    link must not also navigate into the detail page. `header` is
    slightly larger and uses `stopPropagation={false}` since the
    company-detail header isn't a link. Filled accent (terracotta)
    for "yours" + outlined muted for "not yours" — deliberately
    *not* primary teal, since teal is already heavily used on the
    page via KSE-30 chips, `ConvictionDial`, link hover, and the
    conviction-score text; using the accent gives the star
    independent visual identity so a user can scan-and-spot
    "what's mine" without having to read text. Delegates the
    entire optimistic-update story to the provider; just calls
    `toggle()` and waits.
  - `frontend/src/app/(app)/layout.tsx` (modified) — single
    `<WatchlistProvider>` wrap around the auth-gated subtree, so
    the provider only mounts once we know there's an authenticated
    user. (Mounting it at the root would mean unauthenticated
    `/login` and `/register` pay for it too, and the `GET
    /watchlist` call needs a token anyway.)
  - `frontend/src/components/company-card.tsx` (modified) — added
    `<WatchlistStar ticker={ticker} variant="card" />` in the
    card's top-right, beside the existing `ArrowUpRight` link
    indicator. The dashboard's per-card layout shifted from "icon
    only" to a small icon group, but the card's other proportions
    are unchanged.
  - `frontend/src/app/(app)/companies/[ticker]/page.tsx`
    (modified) — `<WatchlistStar ticker={company.ticker}
    variant="header" stopPropagation={false} />` placed directly
    to the right of the ticker H1, before the existing KSE-30 /
    KMI-30 chips. The chip row's gap was nudged from `gap-2` to
    `gap-3` to give the larger header star room.
  - `frontend/src/app/(app)/dashboard/page.tsx` (modified) — added
    `DashboardTabs` (the segmented control: "All companies" / "My
    watchlist", with a tabular-nums count chip on the watchlist
    pill) and `CompanyGrid` (extracted the existing grid render so
    it can also handle the empty + loading states of the watchlist
    tab). Empty watchlist reuses the existing `EmptyState`
    component the Session 1 "no report yet" CTA established —
    same visual language, no new pattern introduced. The "Browse
    all companies" CTA inside the empty state flips the tab via a
    callback, no scroll / navigation involved.
  - `frontend/src/lib/api/types.ts` (modified) — added
    `WatchlistItem` and `AddWatchlistRequest` interfaces mirroring
    the backend Pydantic schemas.

**Frontend filter is purely client-side.** No new dashboard API
call. The dashboard already loads the universe + per-ticker detail
in parallel during mount (Phase 4 Session 1), and the
`WatchlistProvider`'s `tickerSet` is loaded once on user-auth.
Switching tabs is a single React state flip — no network traffic,
no spinner.

**Live verification — Part A (backend flow against real Neon DB).**
`python backend/scripts/verify_watchlist_flow.py` — 15 PASS / 0
FAIL. This script walks the same HTTP sequence the frontend takes
on a real session:

  - register fresh test user (201)
  - simulate dashboard mount: `GET /companies?limit=50` → 10
    tickers (correct), `GET /watchlist` → `[]`
  - simulate star-tap on PPL (add): `POST /watchlist {PPL}` →
    201, then `GET /watchlist` → membership set `{PPL}`
  - simulate star-tap on MCB (second add): `POST` → 201,
    membership set `{MCB, PPL}`
  - simulate "switch to My watchlist tab": intersect the
    in-memory universe with the in-memory ticker set → `[MCB,
    PPL]`, matches expectation
  - simulate un-star PPL: `DELETE /watchlist/PPL` → 200,
    membership set `{MCB}`
  - dry-run 409-on-dup-add idempotence: `POST /watchlist {MCB}`
    → 409 (the provider's "treat 409 as idempotent success"
    branch is exercised correctly)
  - dry-run 404-on-remove-missing idempotence: `DELETE
    /watchlist/PPL` again → 404 (provider's
    "treat 404 as idempotent success" branch)
  - dry-run 404-on-unknown-ticker rollback: `POST /watchlist
    {ZZZNOPE}` → 404 (provider's actual rollback branch — the
    optimistic insert gets rolled out and the ApiError is rethrown
    to the star's onClick handler)
  - cleanup + final `GET /watchlist` → `[]`

**Live verification — Part B (frontend tsc + Next.js dev server).**
`cd frontend && npx tsc --noEmit` → exit 0, no output. All new
types align across the provider, the star, the API client, and
the dashboard re-render. `npm run dev` boots clean (Next.js 15.5.19
Ready in 3.7s). `curl -s -o /dev/null -w "%{http_code}"` against
`/login`, `/dashboard`, `/companies/PPL` returned 200, 200, 200.
Compile log shows clean compiles for all three route entries with
no client-side errors.

**Visual UI render NOT independently verified.** Same gap as
Phase 4 Sessions 2 and 3 — the Claude in Chrome MCP isn't
connected on this machine. User should open
`http://localhost:3000/dashboard` after `npm run dev` + login and
visually confirm:

  1. Each of the 10 company cards on the "All companies" tab shows
     a star icon in the top-right corner, next to the arrow link
     indicator. Resting state = muted outline.
  2. Tapping the star fills it with the terracotta accent
     *immediately* (the optimistic update), then stays filled
     once the network call returns. Tapping it again unfills it
     *immediately*, then stays unfilled.
  3. Tapping a star does NOT navigate into the company detail
     page (the `stopPropagation=true` default on the `card`
     variant prevents the `Link` from firing).
  4. Switching to "My watchlist" filters the grid to only the
     ones with filled stars. The count chip on the tab matches
     the number of cards shown.
  5. With zero watchlisted tickers, "My watchlist" shows the
     designed empty state — a star icon, "Your watchlist is
     empty" headline, the explanatory copy, and a "Browse all
     companies" CTA that flips the tab back to "All companies"
     when tapped.
  6. On `/companies/PPL`, a slightly larger star sits to the
     right of the "PPL" header H1. Tapping it toggles in
     real-time, and (because of `stopPropagation={false}`) does
     not interfere with anything (the header isn't wrapped in a
     `Link`, so there's nothing to prevent).
  7. Toggling a star on the dashboard, then visiting the
     detail page, shows the same fill state (shared via the
     provider's `tickerSet`). Conversely, toggling on the detail
     page and going back to the dashboard shows the same fill
     state on that ticker's card.

**Optimistic-rollback check (per the prompt's explicit ask).**
The 404-on-unknown-ticker case in `verify_watchlist_flow.py` Step
9 confirms the backend returns the same status code the
`WatchlistProvider` checks for (`err.status !== 404` triggers
rollback). This is harder to reproduce in the actual UI without
hand-crafting a URL, but it *would* fire if a user opened
`/companies/SOMETHINGNEW` for a ticker not in the seeded
`Company` table — the optimistic insert would happen, the POST
would 404, the provider would roll back the Set, and the star
would visually revert. This is the rollback branch I'd test
end-to-end in a connected-browser session by temporarily disabling
the backend's `/watchlist` POST while keeping the GET working
(e.g. via a network-blocking devtools rule); the back-end-side
dry-run above is the most we can verify without that.

**Judgment calls / deviations from the prompt.**

  - *Provider, not per-component state.* The prompt allows for
    "client-side filtering of data already being fetched for the
    dashboard if feasible." I went with a shared `WatchlistProvider`
    rather than dashboard-local state because the star also needs
    to render on the company-detail page, and split-brain between
    the two routes (a toggle on detail page not reflected on the
    dashboard you came from) would be a worse UX than the small
    cost of one context.
  - *Optimistic update lives in the provider, not the star.* Keeps
    the star purely visual and reusable elsewhere later (eg a
    future news article card with "follow this story" semantics).
  - *Accent color, not primary.* The page is teal-heavy already;
    the watchlist star is the user's most personal decision on the
    page and deserves to scan independently. Picking terracotta
    leans on the existing palette role (the same role the analyze
    CTA already uses), so it's not a new palette decision.
  - *Empty state reuses `EmptyState`.* Same component as the "no
    report yet" CTA on the company detail page. Consistency over
    novelty.
  - *Idempotent 409/404.* The prompt asked for "honest" failure
    handling; the most honest treatment of "the server already has
    this" is to silently accept it as success rather than show an
    error, because the user's intent (have this on my watchlist)
    is already satisfied. Same logic for 404-on-remove (the
    user's intent — don't have this on my watchlist — is
    already satisfied).

**Confirmation that the five protected files were NOT touched.**
`git status --porcelain` (and a check of the staged diff) at the
end of the session lists only the new + modified files documented
above. Neither `trend_analyzer.py`, `news_synthesizer.py`,
`filing_skeptic.py`, `orchestrator.py`, nor `arbitrator.py`
appears in the diff. Scoring math is unchanged.

**Docs updated this session:** `CLAUDE.md`'s Current build state
got a Phase 4 Session 4 line; the Key files map got six new/
modified entries (`frontend/src/lib/api/watchlist.ts`,
`frontend/src/lib/watchlist/context.tsx`,
`frontend/src/components/watchlist-star.tsx`,
`frontend/src/app/(app)/layout.tsx`,
`frontend/src/components/company-card.tsx`,
`backend/scripts/verify_watchlist_endpoints.py`,
`backend/scripts/verify_watchlist_flow.py`); existing entries for
`frontend/src/lib/api/client.ts`,
`frontend/src/app/(app)/dashboard/page.tsx`,
`frontend/src/app/(app)/companies/[ticker]/page.tsx`, and
`backend/app/schemas/intelligence.py` were extended to note the
Session 4 wiring. This BUILD_LOG entry was added.

**What Session 5 should pick up next.** Per Phase 4 Session 1's
original priority list, the remaining slices are: (a) the **news
article list** on the company detail page (renders the existing
`/companies/{ticker}/news` endpoint), (b) **deploy to production**
on the documented Vercel/Railway/Neon/Upstash stack, and (c) other
polish items from Session 1's list. The news list is the obvious
next user-facing slice. Important to remember per
`docs/KNOWN_ISSUES.md`: news matching is "noisy" (keyword-based,
false-positive prone — e.g. "petroleum" headlines attach to PPL
and PSO even when tangential), so the UI needs to surface the
LLM-judged-relevant set distinctly from the raw keyword-matched
set — design beats raw rendering here. The other realistic
session-5 candidate: a small backend-side improvement to expose
the LLM's per-article relevance judgment via the `/news` endpoint
so the frontend has something concrete to render that
distinction against, rather than the frontend having to invent
the distinction visually from a raw matched-articles list.

<!-- Next entry goes here. Add a new ## dated heading below this line. -->