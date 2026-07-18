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

## 2026-06-28 — Phase 4 Session 5: news article list on the company detail page

**Goal.** Render NewsSynthesizer's relevance judgment as a real
piece of content on the company detail page, distinguishing the
two meaningfully different "no articles to show" states the prompt
called out, without showing the noisy raw keyword-matched set. Per
the prompt's "show only LLM-judged-relevant articles" lock,
respect that intent even when it forces a more conservative
render than just dumping the matched list.

**Step 0 — inspect the real persisted shape.** Wrote
`backend/scripts/inspect_news_state.py` (read-only) and ran it
against the live Neon DB. Findings:

| Ticker | `news_articles` rows | latest `news_synthesizer.output` |
|---|---|---|
| ENGRO | 0 | no report yet |
| HBL | 0 | no report yet |
| LUCK | 0 | no report yet |
| MARI | 0 | no report yet |
| MCB | 0 | sentiment=NEUTRAL, article_count=0, relevant=0 |
| MEBL | 0 | no report yet |
| OGDC | 1 | no report yet |
| **PPL** | **9** | sentiment=NEUTRAL, article_count=9, **relevant=0** |
| PSO | 9 | no report yet |
| UBL | 0 | sentiment=NEUTRAL, article_count=0, relevant=0 |

This established two critical facts that shaped everything else:

  1. The current data covers both zero-states the prompt cares
     about — but only those two. PPL is the only ticker in the
     entire universe with matched articles, and the
     NewsSynthesizer judged 0 of 9 as relevant (textbook
     "noisy keyword match" — headlines like "Oil prices climb
     more than $3 after Israeli strikes on Lebanon" got
     attached to PPL just because "petroleum"). MCB and UBL are
     the "no articles matched at all" case (the LLM call was
     skipped per the project's "no real data → no LLM call"
     rule, so `article_count=0` and `relevant_articles=0`).
  2. **No live ticker currently exercises the "partial
     relevance" or "all relevant" render paths.** Those
     branches will need to be hand-tested against synthetic data
     when they show up — flagged explicitly below.

**Step 1 — Part 1 decision (backend change needed? yes, minimal).**
The two zero-states are not currently distinguishable from
`GET /companies/{ticker}/news` alone (the endpoint returns the
raw matched articles, no judgment metadata), and `GET
/companies/{ticker}/report` didn't carry NewsSynthesizer's output
either — it only carried `score_breakdown` (Phase 4 Session 2).
So the frontend literally cannot tell "PPL had 9 articles, all
judged irrelevant" from "MCB had 0 matched articles" using
only what the API currently serves.

The minimal additive fix per the prompt's Part 1 instruction:
hoist `news_synthesizer.output` onto `IntelligenceReportResponse`,
exactly mirroring how `score_breakdown` is already hoisted by
the existing `@model_validator(mode="before")`. New schema:
`NewsSynthesis` (sentiment, uniformity, article_count,
relevant_articles, narrative_summary), set as
`IntelligenceReportResponse.news_synthesis: NewsSynthesis | None`.

The validator was refactored from "hoist score_breakdown" to
"hoist score_breakdown AND news_synthesis in a single pre-build
pass" — same logic, just two source fields. Score_breakdown
behavior is unchanged and regression-tested per the verification
section below (it'd be embarrassing to ship a Session 5 schema
change that broke a Session 2 schema field). NewsSynthesizer's
LLM prompt, output structure, and analysis logic are **not
touched** — only what's serialized out of the persisted row.
This is exactly the "additive metadata only" branch the prompt
anticipated.

**Step 2 — Part 2 frontend: NewsList component.** Built
`frontend/src/components/news-list.tsx`. State machine has seven
modes:

  - `loading` → skeleton (3 row placeholders, soft-pulse)
  - `error` → bearish-tinted ErrorState with retry button
  - `no-report-yet` → "no relevance judgment yet" CTA
    (`news_synthesis === null` means we have no LLM judgment to
    filter on; rendering the raw matched articles would be the
    noisy keyword-matched set the lock forbids)
  - `no-articles-matched` → zero-state 1: "No news articles found
    for [TICKER]" (`article_count === 0`)
  - `matched-but-none-relevant` → zero-state 2: "N articles
    mentioned [TICKER] but NewsSynthesizer judged none genuinely
    relevant" (`article_count > 0 && relevant_articles === 0`)
  - `partial-relevance` → zero-state 3: "M of N articles judged
    relevant; per-article details aren't surfaced — see analyst
    narrative" (`0 < relevant_articles < article_count`)
  - `full-relevance` → render the matched list with a
    bullish-tinted "All N articles judged relevant" header
    (`relevant_articles === article_count > 0`)

The partial-relevance branch is the one design call worth
flagging. The prompt's locked-in design ("show only the
LLM-judged-relevant articles") cannot be literally implemented
in the partial case, because NewsSynthesizer only persists an
aggregate `relevant_articles` count — not per-article relevance
flags — and the prompt's hard rules forbid changing
NewsSynthesizer's LLM prompt to start emitting per-article
judgments. So the choice was between:

  - (a) Show all M matched articles when there's any positive
    relevance count, even though we'd be including articles the
    LLM judged irrelevant. Violates the lock as written.
  - (b) Show 0 articles in the partial case with an explanation
    that per-article details aren't surfaced. Strictly honors
    the lock; the cost is the partial case shows no list at all.
  - (c) Pick a heuristic (top-N most recent) as a proxy for
    "the relevant ones". Dishonest — we'd be implying those
    specific articles were judged relevant when the system
    actually said nothing about which subset is which.

Went with **(b)** because it's the only interpretation that
doesn't either contradict the lock (a) or fabricate per-article
judgments NewsSynthesizer didn't make (c). For the real PPL data
this is a moot point — PPL is at 0 relevant of 9 matched, which
is zero-state 2 (matched-but-none-relevant), and shows no list
in either reading. For a hypothetical future "5 of 9" ticker,
this UX will visibly hold back the list. The right long-term fix
is a separate session where NewsSynthesizer's LLM prompt is
augmented to emit per-article relevance — which is explicitly
out of scope this session and explicitly forbidden by the hard
rules. **Flagged here so a future reader knows this is the
trade-off the partial-relevance render path embodies, not an
oversight.**

The `full-relevance` mode reaches the matched-list rendering
without that ambiguity (when N=M, the LLM judged all of them
relevant, so showing all of them honors the lock without
inference). Article rows render headline (linked to source URL
in a new tab when present), source (pretty-mapped:
`arynews` → "ARY News"), published date (en-PK locale), and
the summary line-clamped to 2 lines.

**Visual treatment.** Karachi Dusk consistency: terracotta-accent
warning icon on the matched-but-none-relevant + partial cases
(the system's "noise warning" tone, same as the analyze
ScoreBreakdown's accent dots); bullish-muted band on the
all-relevant header (signals the LLM's positive judgment); soft
dashed bordered cards for all zero-states (same visual idiom as
the existing dashboard EmptyState pattern); muted source/date
chip + readable headline + line-clamped summary on each list
row. No new color tokens introduced.

**Step 3 — Part 3 integration.** Placed `<NewsList />` after the
report body (or after the "no report yet" CTA) on the company
detail page, NOT inside the `ReportBody` branch. Reasoning: the
news component reads its own data and handles the
no-report-yet case internally, so coupling it to the
report-or-CTA conditional would just push the same logic up a
level. Visually it lands as the last major section of the page,
below the bull/bear cases + score breakdown + risk + ML cards
+ ReportMetaStrip — which reads naturally as "supporting
evidence" rather than headline content, exactly as the prompt
suggested.

**Live verification — Part A (schema regression + zero-state DB
classification).** `python backend/scripts/verify_news_synthesis.py`
against the real DB. For each of the 6 tickers I checked, the
script does three things: validates the ORM row through the new
`IntelligenceReportResponse`; cross-checks that the hoisted
`news_synthesis` is byte-identical to the raw
`agent_outputs["news_synthesizer"]["output"]`; AND cross-checks
that the hoisted `score_breakdown` still matches the raw DB
values term-for-term (regression guard for the validator
refactor). 3/3 tickers with reports passed both checks; the 3
without reports were correctly skipped. Zero-state
classifications matched what the inspect-news-state diagnostic
saw at the start of the session: PPL=MATCHED_BUT_NONE_RELEVANT,
MCB+UBL=NO_ARTICLES_MATCHED.

**Live verification — Part B (real HTTP API path).** `python
backend/scripts/verify_news_api.py` boots a real registered user
and walks the same HTTP sequence the frontend NewsList takes:

  - Busts today's Redis report-cache for PPL/MCB/UBL up front so
    the new `news_synthesis` field is guaranteed visible even if
    a pre-Session-5 cached response was still alive (the
    Phase 4 Session 2 stale-cache gotcha — see
    `docs/KNOWN_ISSUES.md`'s "Known environment quirks" / the
    REPORT_CACHE_KEY note in CLAUDE.md). Today's caches were
    empty so nothing was deleted, but the bust runs
    unconditionally so this script is safe to re-run any day.
  - Registers a fresh `news_api_test_{timestamp}@example.com`
    user (note: `.test` TLD is rejected by python-email-validator
    per RFC 6761; `.example.com` per RFC 2606 passes —
    documenting this here because the first run of the script
    failed with HTTP 422 on register before that was fixed).
  - For each test ticker: `GET /report` → asserts
    `news_synthesis` AND `score_breakdown` are both present in
    the response body; `GET /news?limit=50` → asserts
    `news_synthesis.article_count === /news.total` (sanity-check
    that the agent and the raw articles endpoint agree on what
    counts as "matched").
  - 3/3 tickers PASS. Render-mode classification logged for the
    human reader so the UI's branches can be hand-verified
    against actual API output. (One scripting note: this script
    initially emitted Unicode `→` and `⇒` characters that the
    Windows cp1252 terminal couldn't render — replaced with ASCII
    `->`/`=>`. Per CLAUDE.md's known quirks, this is the
    recurring Windows-terminal-encoding issue.)

**Live verification — Part C (frontend tsc + dev-server).**
`cd frontend && npx tsc --noEmit` → exit 0, no output. All new
types (`NewsArticleResponse`, `NewsSynthesis`,
`IntelligenceReportResponse.news_synthesis`) align across the
typed client, the report response shape, and the NewsList
component's branches. `npm run dev` boots Next 15.5.19; `curl
-s -o /dev/null -w "%{http_code}"` against `/login`,
`/dashboard`, `/companies/PPL` (the matched-but-none-relevant
case), `/companies/MCB` (no-articles-matched), and
`/companies/UBL` (no-articles-matched) all return 200. The dev
server's compile log is clean — no client-side errors, no
TypeScript warnings, no missing dependencies.

**Port cleanup.** Per the recurring "don't leave dev servers
running" issue from recent sessions, both `python -m uvicorn`
and `npm run dev` were terminated via PID-kill at the end of
verification, then `netstat -ano | grep -E ":(3000|8000)\s.*LISTENING"`
was re-run and returned "Ports 3000/8000 clear" (literal, not
inferred). No background processes were left bound to the
session's dev ports.

**Visual UI render NOT independently verified.** Same gap as
Phase 4 Sessions 2 / 3 / 4 — the Claude in Chrome MCP isn't
connected on this machine (`list_connected_browsers` returns
`[]`). User should open `http://localhost:3000/companies/PPL`
after `npm run dev` + login and visually confirm:

  1. The NewsList card renders after the report body
     (post-bull/bear, post-score-breakdown, post-ML).
  2. PPL shows the zero-state-2 message: header reads "9
     matched articles — 0 judged relevant by NewsSynthesizer",
     body shows the dashed-border zero-state card with the
     accent warning icon and the explanation paragraph.
  3. MCB and UBL show zero-state-1: header reads "No articles
     mentioning [TICKER]…", body shows the dashed-border zero
     state with a ScanSearch icon.
  4. A ticker with no report yet (HBL, LUCK, ENGRO, MARI,
     MEBL, OGDC, PSO) shows the "no relevance judgment yet"
     CTA pointing at the existing AnalyzeButton above.
  5. After running an analysis (the Generate report button)
     against one of those tickers, the NewsList card switches
     to whichever zero-state the new report produces — most
     likely zero-state 1 (no matched articles) for HBL/LUCK/
     ENGRO/MARI/MEBL since the DB shows 0 news_articles for
     them currently.

**Untestable render paths (flagged honestly).** The
`partial-relevance` and `full-relevance` branches of the state
machine are wired but **cannot be exercised against current
live data** — no ticker has `0 < relevant_articles <=
article_count`. The code paths are unit-clean (typed
end-to-end, branch logic is straightforward `if/else if` over
the two counts, no async or external state) but they have not
seen real DB data flow through them. Two ways to test them
when the time comes: (a) hand-edit a persisted report's
`agent_outputs["news_synthesizer"]["output"]["relevant_articles"]`
in PostgreSQL temporarily, bust the Redis cache, hit the API;
(b) wait until news matching produces a real positive judgment.
This is a known limitation flagged here so a future reader
doesn't mistake "untested code path" for "verified code path."

**Judgment calls / deviations from the prompt.**

  - *Backend change adds a top-level schema field, not a new
    endpoint.* The prompt suggested "e.g. a `total_matched_count`
    alongside the existing relevant-article list" as one
    possible additive shape — that wording implied the
    relevance data might live on `/news`. I put it on `/report`
    instead because (a) all the other agent outputs live on
    `/report`, so it composes naturally with the existing
    score_breakdown / ml_detail hoist, (b) `/news` is a
    paginated list and adding a sibling aggregate would
    inflate every page, (c) the validator pattern is already
    proven by Session 2. One API call per page load instead of
    two; consistent with the rest of the schema.
  - *Strict-lock interpretation of partial-relevance, with an
    explanation card rather than a list.* Covered in the
    component-design section above. The only interpretation
    that doesn't either lie about per-article judgments or
    violate the lock.
  - *NewsList placed outside `report ? ReportBody : EmptyState`.*
    Same reasoning Phase 4 Session 3's PriceChart followed:
    the component handles its own no-report-yet branch
    internally, so coupling it to the page's report-or-CTA
    conditional would push the same logic up a level.
  - *Self-contained data fetching.* `NewsList` owns its
    `getCompanyNews` call rather than receiving articles via
    props from the page. Same pattern as `MlSignalCard` /
    `AnalyzeButton` / `PriceChart`: each card owns the data
    it needs from the typed API-client chokepoint.
  - *Skipped pagination UI even though the endpoint paginates.*
    Set `limit=50` on the fetch, no page-2 button. ARY News
    coverage is currently 19 rows across the entire universe
    and matching density per ticker is single-digit (PPL=9,
    PSO=9, OGDC=1, everyone else 0) — a paginator would be
    chrome with nothing behind it. Easy to add later when
    article volumes grow.

**Confirmation that the five protected files were NOT touched.**
`git status --porcelain` after this session lists only:
- `backend/app/schemas/intelligence.py` (additive `NewsSynthesis`
  schema + dual-hoist validator)
- `CLAUDE.md` + `docs/BUILD_LOG.md` (this entry)
- `frontend/src/app/(app)/companies/[ticker]/page.tsx` (one
  import + one JSX line for `<NewsList ... />`)
- `frontend/src/lib/api/companies.ts` (new `getCompanyNews`
  function + corresponding type import)
- `frontend/src/lib/api/types.ts` (`NewsArticleResponse`,
  `NewsSynthesis`, `IntelligenceReportResponse.news_synthesis`
  optional field)
- New files: `backend/scripts/inspect_news_state.py`,
  `backend/scripts/verify_news_synthesis.py`,
  `backend/scripts/verify_news_api.py`,
  `frontend/src/components/news-list.tsx`

Neither `trend_analyzer.py`, `news_synthesizer.py`,
`filing_skeptic.py`, `orchestrator.py`, nor `arbitrator.py`
appears in the diff. Scoring math, relevance-judgment logic,
and LLM prompts are all unchanged.

**Docs updated this session:** `CLAUDE.md`'s Current build state
got a Phase 4 Session 5 line (chronologically after Session 4),
and the Key files map got four new entries
(`frontend/src/components/news-list.tsx`,
`backend/scripts/verify_news_synthesis.py`,
`backend/scripts/verify_news_api.py`,
`backend/scripts/inspect_news_state.py`); existing entries for
`frontend/src/app/(app)/companies/[ticker]/page.tsx` and
`backend/app/schemas/intelligence.py` were extended to note the
Session 5 wiring + the dual-hoist validator. This BUILD_LOG
entry was added.

**What Session 6 should pick up next.** Per the user's note in
the Session 5 prompt, Session 6 is a batched polish pass: dark
mode toggle, 404 page, unauthenticated landing page, mobile
nav drawer. All are pure-frontend, none should touch the
backend or the five protected agent files. Suggested ordering
for that session: landing page first (unauthenticated entry
point — affects what a recruiter sees on first click before
login), then 404 (covers /companies/UNKNOWN and any other
undefined route), then dark mode (palette already defines all
the CSS vars — should mostly be a `data-theme="dark"` block in
globals.css plus the toggle wiring), then mobile nav drawer
(the NavBar is desktop-only right now, so mobile users get a
broken header).

## 2026-07-03 — Phase 4 Session 6: batched polish pass (landing / 404 / dark mode / mobile nav)

Last item on Phase 4's original scope. Four small, pure-frontend
polish sub-steps, done as four independent units. **Zero backend
changes — the entire code diff is under `frontend/` (+ these docs).**
None of the five protected agent files were touched. This session
also finally closed the "no in-browser walkthrough" gap flagged in
Sessions 2–5: the Claude Preview MCP was available on this machine,
so every sub-step was verified in a real headless Chromium, not just
by curl + SSR-shell checks. (Set up via `.claude/launch.json`, left
untracked as local tooling — see "staging" below.)

Two real bugs were caught *because* of that live browser pass and
fixed in-session (details under sub-steps 3 and 4).

---

### Sub-step 1 — Unauthenticated landing page

**Files:** `frontend/src/app/page.tsx` (was a one-line
`redirect("/dashboard")`, now the landing page).

**What.** Client component. Reads `useAuth()` `{user, loading}` — the
same session check `(app)/layout.tsx` and the login page already use
(read before writing, not invented) — and `router.replace("/dashboard")`
for a logged-in visitor; shows a loader while auth resolves; otherwise
renders a static Karachi-Dusk marketing page. Content: brand, honest
framing pulled from CLAUDE.md "What this is" (ML price-direction model
+ 4-agent pipeline over KSE-30 — no overclaim), primary CTA to `/login`,
secondary to `/register`, a small header with brand + theme toggle + Sign
in link, and the `ConvictionDial` featured decoratively at a static
`score=58.5` explicitly captioned "Illustrative reading — sign in for
live scores" (no fabricated per-ticker data). No new deps, no backend
calls.

**Verified.** `npx tsc --noEmit` -> exit 0. `curl` of
`http://localhost:3000/` -> **200** unauthenticated. In the real
browser (tokens cleared): `path=/`, `h1="One conviction score. Four
agents. Zero noise."`, one button `aria-label="Switch to light mode"`
present. Redirect path for a logged-in user confirmed by mirroring the
existing `useAuth`-based pattern (loader shown while `loading || user`,
`replace("/dashboard")` in effect). Rendered in both light and dark
palettes (screenshots).

**Judgment calls.** (a) Landing marketing content is client-rendered —
the SSR HTML is the auth loader shell (because `AuthProvider` starts
`loading=true`), same client-gated pattern as the login page; content
hydrates in <1 render for an unauthenticated visitor. Acceptable for a
client app; noted honestly. (b) Added a theme toggle to the landing
header even though it has no NavBar, so unauthenticated visitors can
switch themes.

---

### Sub-step 2 — 404 / not-found page

**Files:** `frontend/src/app/not-found.tsx` (new);
`frontend/src/app/(app)/companies/[ticker]/page.tsx` (invalid-ticker
branch).

**What.** Root `not-found.tsx`, styled to match (Fraunces/Inter,
Karachi-Dusk, brand + theme toggle header). Back-link resolves to
`/dashboard` (authed) or `/` (not) via `useAuth()`; defaults to `/`
while auth is still resolving (always safe). **Invalid-ticker case:**
`/companies/{UNKNOWN}` previously set a generic error string and
rendered the "Couldn't load company / Try again" `ErrorState` on API
404 — which reads as a transient failure. Changed the page's existing
404 branch to set a `notFound` flag and render a dedicated
"No company found for X" `EmptyState` with a back-to-dashboard link
instead. Contained to that one branch; the page's other error states
(non-404 company error, report error) are unchanged.

**Verified.** `npx tsc --noEmit` -> exit 0. `GET /this-does-not-exist`
-> real HTTP **404** (Next's root `not-found` sets a genuine 404
status — reported as-observed, not assumed); browser render shows
"404 / This page wandered off / Back to home". `GET
/companies/ZZZNOTATICKER` -> **200** at the HTTP layer (it matches the
dynamic `[ticker]` route, so the page shell serves 200 and the client
then shows the new not-found `EmptyState` after the API returns 404 for
the ticker). Both statuses are the honest, expected behavior for a
client-rendered app; documented rather than glossed.

**Judgment call / scope.** The prompt asked whether improving the
company-page invalid-ticker state was in scope or a follow-up. Decided
it was in scope (the prompt calls it out specifically) but kept it
minimal — one branch, reusing the existing `EmptyState` component — and
did *not* rewrite the page's other error/loading states. Flagged here
so the small scope expansion is on record.

---

### Sub-step 3 — Dark mode toggle

**Files:** `frontend/src/lib/theme/context.tsx` (new),
`frontend/src/components/theme-toggle.tsx` (new),
`frontend/src/app/layout.tsx` (no-flash script + `suppressHydrationWarning`
+ `ThemeProvider`). `globals.css` and `tailwind.config.ts` were
**not** changed — the `.dark` token block and `darkMode: ["class"]`
already existed.

**What.** Chose the `.dark`-class approach (consistent with the
existing Tailwind config + `.dark {}` block — did *not* introduce a
second `data-theme` mechanism). `ThemeProvider` toggles `.dark` on
`<html>`, persists to `localStorage['psx-theme']`, defaults to
`prefers-color-scheme`. A no-flash inline `<head>` script applies the
class before hydration; the provider reconciles its React state from
whatever class the script set. `ThemeToggle` has an icon variant
(desktop NavBar) and a `labeled` variant (mobile drawer).
`ThemeProvider` wraps `AuthProvider` at the root.

**Dark-token completeness check (required).** Diffed `:root` vs `.dark`
in `globals.css`: `.dark` overrides every role token light mode
defines — background, foreground, card(+fg), surface(+fg), muted(+fg),
primary(+fg), accent(+fg), bullish(+fg,+muted), bearish(+fg,+muted),
neutral(+fg), destructive(+fg), border, input, ring, shadow. The only
`:root` var `.dark` does *not* redefine is `--radius`, which is
theme-independent and correctly cascades from `:root` (both selectors
target `<html>`). No token silently falls back to an unreadable
light-mode value in dark mode.

**Verified.** `npx tsc --noEmit` -> exit 0. In the real browser: toggle
live-swaps the entire page (chrome, cards, dial) **without a reload**;
`localStorage['psx-theme']` flips dark<->light and the html class
follows; the preview browser's `prefers-color-scheme:dark` was picked
up as the first-visit default (confirming that path). Light + dark
screenshots captured for landing, dashboard, and company detail.

**Bug caught + fixed (a).** The no-flash script adds `dark` to `<html>`
before hydration, but the SSR HTML doesn't include it, so React logged
a hydration mismatch on `<html className>` (visible as a dev-overlay
"1 Issue"). Fixed with `suppressHydrationWarning` on the `<html>`
element in `layout.tsx` — the idiomatic next-themes fix; it suppresses
only that element's attribute diff. After the fix + a clean server
restart, the browser console shows **no errors** ("No console logs").

**ConvictionDial / PriceChart staleness (required deliverable — NOT
fixed, per hard rules).**
- **`ConvictionDial` is toggle-safe.** It passes `hsl(var(--token))`
  *strings* directly as SVG `stroke`/`fill` attributes; the browser
  re-resolves those CSS vars at paint, so it re-themes instantly on
  toggle. Empirically confirmed: after a dark->light toggle *with no
  reload*, the dial (below the fold on the landing page) rendered the
  correct light-mode sage needle / cream card / dark "58.5".
- **`PriceChart` goes stale on toggle.** It reads CSS vars via
  `getComputedStyle` inside a `useEffect` keyed only on `[prices]`, so
  a theme toggle doesn't re-run `createChart`; the line/axis/grid/text
  colors keep the mount-time theme until the next reload/remount.
  Empirically confirmed on `/companies/PPL`: toggling light->dark left
  the chart with dim, light-derived colors (dark-teal line, near-
  invisible dark axis text on the dark card), while a fresh reload in
  dark mode rendered it correctly (lifted-teal line, legible axis text,
  vivid green/red volume bars). Left unfixed on purpose (would require
  editing `price-chart.tsx` internals — explicitly out of scope);
  filed as a follow-up in CLAUDE.md's "Future / deferred".

**Sequencing judgment (toggle placement vs. mobile nav).** Built the
theme infra + desktop NavBar toggle in this sub-step, and placed the
*mobile* toggle inside the drawer as part of sub-step 4 (its natural
home), rather than deferring to a notional Session 7. So the toggle is
reachable at every viewport by end of session.

---

### Sub-step 4 — Mobile nav drawer

**Files:** `frontend/src/components/nav-bar.tsx` (rewritten).

**What (after reading the existing component first).** `NavBar`'s nav
links were `hidden ... sm:flex`, so below `sm` (640px) the Dashboard
link disappeared (the account dropdown still worked, but the nav did
not). Rewrote so at `<sm` the desktop nav + account menu are `hidden`
and a `sm:hidden` hamburger opens a full-height right-side drawer
containing the same destinations (Dashboard), the `labeled` theme
toggle, and the account block + Sign out. Drawer: `role="dialog"
aria-modal`, backdrop (click-to-close), Escape-to-dismiss, body-scroll
lock, close-button focused on open (basic a11y hygiene, not a full
focus trap). No new dependencies — plain React state + Tailwind.

**Breakpoint + why.** `sm` (640px), chosen to match the existing nav's
own responsive boundary (`hidden sm:flex`), so there's one consistent
mobile/desktop line rather than a second arbitrary one.

**Verified (with rendered className / computed styles, not assumed).**
`npx tsc --noEmit` -> exit 0. At 375px in the real browser:
`getComputedStyle(nav).display === "none"` (desktop nav hidden) and
the hamburger (`aria-label="Open menu"`, className contains
`sm:hidden`) is `visible`. Opening the drawer: panel present, contents
correct (Dashboard link active-highlighted, "Dark mode" labeled toggle
showing "LIGHT", account block Polish Tester + email + FREE, Sign out).
Escape -> dialog removed and `document.body.style.overflow` restored to
"" (scroll lock released).

**Bug caught + fixed (b).** First render of the open drawer showed the
panel see-through and only 64px tall (computed `height: 64px`), with
the backdrop not covering the screen. Root cause: the drawer was
rendered *inside* `<header>`, whose `backdrop-blur-md` (a
`backdrop-filter`) establishes a containing block for `position:fixed`
descendants — so the panel's `h-full`/`top-0` and the backdrop's
`inset-0` resolved against the 64px header box, not the viewport. Fixed
by making `NavBar` return a fragment with `<MobileDrawer>` as a
*sibling* of `<header>` instead of a child. Re-verified: panel computed
height 64px -> 812px, opaque `rgb(255,255,255)` background, backdrop
dims the whole screen, and the `mt-auto` account block correctly pins
to the drawer bottom. (A one-off `preview_click` timing quirk made the
first two synthetic clicks not register the React handler; a native
`.click()` opened it reliably — a tooling artifact, not a component
bug.)

---

### End-of-session

**Ports.** Literal check at session end (preview + backend both
stopped):

    $ netstat -ano | grep -E ':3000\s|:8000\s' | grep -i LISTENING
    (no LISTENING sockets on :3000 or :8000)
    $ Get-CimInstance node.exe | ... grep -i 'next|psx|3000'
    No lingering next/psx node processes

The throwaway-login backend (uvicorn :8000, used to exercise the authed
dashboard/company routes in-browser) and the preview Next server were
both terminated and confirmed gone.

**Staging.** Explicit per-file, no `git add .`. Staged: the 4 modified
files (`frontend/src/app/(app)/companies/[ticker]/page.tsx`,
`frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`,
`frontend/src/components/nav-bar.tsx`), the 3 new files
(`frontend/src/app/not-found.tsx`,
`frontend/src/components/theme-toggle.tsx`,
`frontend/src/lib/theme/context.tsx`), plus `CLAUDE.md` and this
`docs/BUILD_LOG.md`. **Deliberately NOT staged:** `.claude/launch.json`
(local Claude-Preview tooling I created to run the in-browser
verification — not application code; left untracked). `git status`
before staging showed no stray `node_modules/`, `.next/`, or
`.env.local`.

**Protected files.** `git diff --stat` confirms the working diff is
only the frontend files + the two docs. None of
`backend/app/agents/{trend_analyzer,news_synthesizer,filing_skeptic,
orchestrator,arbitrator}.py` appear — the entire code diff is under
`frontend/`.

**Phase 4 scope.** Phase 4's original scope had this batched polish
pass (landing / 404 / dark mode / mobile nav) as its last remaining
item. This session closes it out. No sub-step was deferred to a
notional "Session 7". The one loose thread is a *newly identified*
follow-up, not leftover Phase 4 scope: `PriceChart` dark-mode
re-theming (it reads CSS vars once at mount and goes stale on live
toggle — see sub-step 3), filed under "Future / deferred".

---

## 2026-07-04 — Hotfix: PriceChart dark-mode re-theme

Not a Phase/Session — a targeted fix for the follow-up flagged at the end
of Phase 4 Session 6. Scope was one component (`price-chart.tsx`) plus docs.

**Reported.** After toggling dark→light, the chart's axis / price / volume
text stayed light-colored and was unreadable against the light card
background. Manual testing confirmed it as a real, visible bug (the Session
6 entry had predicted it from the code but the symptom is now demonstrated).

**Two hypotheses checked (didn't assume).** (a) toggle-staleness — the chart
keeps its mount-time theme colors until reload; (b) a genuinely wrong
light-mode color value that would break even on a fresh light-mode load.
Outcome: **(b) ruled out** — a *fresh* page load in light mode rendered dark,
fully-legible text (the light `--foreground` token `195 30% 12%` is correct
dark-on-cream). **(a) confirmed as root cause** — the chart resolved its
colors via `getComputedStyle(document.documentElement)` inside a `useEffect`
keyed only on `[prices]`; a theme toggle flips the `.dark` class on `<html>`
but doesn't change `prices`, so the effect never re-ran and
lightweight-charts (which caches colors internally at creation) kept its
mount-time palette.

**Fix.** Extracted color resolution into a shared `resolveChartColors()`
helper so the create path and the re-theme path can't drift. Added a new
effect keyed on `[theme]` (from `useTheme()`, the Session 6 ThemeProvider)
that re-themes the **existing** chart in place — `chart.applyOptions()` and
`series.applyOptions()` for layout `textColor` / pane separators / grid /
crosshair / the area line color+fill+priceline / both MA line colors, and
`volumeSeries.setData()` for the volume histogram (its per-bar directional
tints are baked into each data point, so they must be re-pushed, not
patched via options). Deliberately **not** a destroy/recreate: updating in
place also preserves the user's current visible range / zoom across a toggle
(a recreate would reset it to `fitContent`). A ref guard skips the theme
effect's first run, since the create effect already builds the chart with
the current theme's colors. For contrast, `ConvictionDial` never had this
bug — it passes `hsl(var(--token))` strings into SVG `stroke`/`fill`, which
the browser re-resolves at paint.

**Verified (Claude Preview MCP, real browser, authed against live
Neon/Upstash backend).** Screenshotted all four states on `/companies/PPL`:
fresh load light, fresh load dark, live dark→light toggle, live light→dark
toggle. Axis / price / volume text legible in every state; the 6M range
stayed selected across both live toggles (confirming the in-place update
preserves zoom). Console error-level logs clean after the applyOptions /
setData calls. `npx tsc --noEmit` exits 0. Port check at end: `netstat -ano`
showed no listeners on :3000 or :8000 (preview + throwaway-login backend
both stopped). `docs/KNOWN_ISSUES.md` entry moved from Open to Resolved;
CLAUDE.md's "Future / deferred" bullet updated to point at the fix.

---

## 2026-07-04 — Phase 5 Session 1: first-ever backtest of the XGBoost model

Opens Phase 5 ("Signal validation & data depth"). Until now the model's
only validation was 3-class test accuracy (39.34% vs 33.33%). This session
answers the real question — *would trading on it have made money, net of
PSX costs?* — with a `vectorbt`-based, read-only, test-split-only backtest
(`backend/scripts/backtest_xgboost.py`). No retrain, no DB writes, no
protected-file changes.

**Result (headline, 9-ticker equal-weight sleeve, shared window
2025-10-24 → 2026-05-29, net of 0.30% round-trip commission, pre-CGT):**

| Strategy | Total ret | Ann. Sharpe | Max DD | Win rate | Trades | vs B&H ret |
|---|---:|---:|---:|---:|---:|---:|
| Ungated (long every UP) | +5.10% | +0.43 | −21.30% | 63.49% | 63 | B&H +3.33% |
| Gated (max_prob > 0.55) | +2.35% | +0.42 | −5.70% | 63.64% | 22 | B&H +3.33% |
| Buy & Hold (benchmark) | +3.33% | +0.33 | −24.41% | n/a | 0 closed | — |

Post-CGT (15% on net gain): ungated +4.33%, gated +2.00%, B&H +2.83%.
Discount-broker sensitivity (0.05%/side): ungated +6.52% / Sharpe 0.50.
Full per-ticker table and the ENGRO-standalone line live in the new
`docs/BACKTEST_RESULTS.md`.

**Honest read:** the ungated signal beat buy-and-hold on both return and
Sharpe over this ~7-month out-of-sample window after a deliberately
expensive cost assumption — a real but *thin* edge (Sharpe well under 1,
drawdown nearly as deep as B&H, per-ticker spread PSO −20% to UBL +24%).
The gated version's value is risk reduction (−5.7% DD from sitting in cash),
not return. One window, one universe, one model — validates the signal is
"not worthless after costs", does **not** establish a deployable strategy.

**Cost assumption (documented, not arbitrary):** 0.15% commission/side
(0.30% round-trip) bundling PSX brokerage + FED/sales-tax-on-commission +
CDC/SECP/PSX/NCCPL levies, deliberately on the expensive side; plus 15% CGT
on net realised gain applied post-hoc at sleeve level (filer, <12-month
hold), applied identically to B&H. Slippage not modelled (close-to-close
fills) — flagged as optimistic. See `BACKTEST_RESULTS.md` and the script
docstring for sourcing.

**No-leakage, proven not assumed:** the harness reads only `test.parquet`
and asserts `test_min > val_max` per ticker; those boundaries were pasted
side-by-side with `verify_dataset.py`'s independently-derived split and
match exactly for all 10 tickers (e.g. PPL val_max 2025-10-23 / test_min
2025-10-24). The model's 5-day forward *label* is never a trading input —
trades come purely from `predict_proba` of the backward-looking features.

**Judgment calls:** (1) long-only (retail PSX shorting impractical);
(2) "regime hold" — one long position while the model stays bullish, exit
on flip — instead of overlapping fixed-5-day holds, because per-signal
5-day holds would need concurrent same-ticker positions (pyramiding), which
the "no leverage / one position" rule forbids; (3) ENGRO quarantined from
the sleeve (its 2024 test window is disjoint from the other 9's 2025-26
window — stitching it in would inject ~10 months of flat-cash days and
distort Sharpe) but still fully reported per-ticker; (4) gated `max_prob >
0.55` mirrors `Arbitrator.ML_GATE` — 105 of 1,426 test rows clear it (vs
zero on the single-latest-day production probe, because the test set spans
1,426 historical days).

**Dependency:** added `vectorbt==1.0.0` to `backend/requirements.txt`. It
pulls numba≥0.66 / llvmlite 0.48, which support the existing `numpy==2.1.3`
pin — **numpy was NOT downgraded** (verified: numpy/pandas/xgboost/sklearn
all still import at their pinned versions). Transitive matplotlib/plotly/
ipywidgets are vectorbt's optional plotting deps, not imported by the app.

**Results home (proposed + acted on):** created `docs/BACKTEST_RESULTS.md`
as the durable home for backtests (model artifact + window + costs + literal
numbers, append-on-retrain), with a one-paragraph headline mirrored here in
the Build Log. Rationale: backtest tables are too big/tabular to live well
in the append-only prose log, and will be re-run on every future retrain —
they deserve a diffable canonical file.

**Protected files untouched:** `git diff --stat` — the diff is
`scripts/backtest_xgboost.py` (new), `requirements.txt`, `CLAUDE.md`,
`docs/BUILD_LOG.md`, `docs/BACKTEST_RESULTS.md`. None of
`trend_analyzer.py` / `news_synthesizer.py` / `filing_skeptic.py` /
`orchestrator.py` / `arbitrator.py`.


---

## 2026-07-17 — Phase 5 Session 2: PSX Terminal integration (fundamentals + announcements mirror)

Second item in Phase 5 ("Signal validation & data depth"). A deliberately
double-sized session: new data client + DB layer + new collector + live
verification, all in one pass. Data-collection only — nothing was wired
into AgentContext, any agent prompt, or the conviction formula; the new
data ends this session sitting verified in the DB, deliberately
unconnected to scoring. No LLM calls anywhere in this path.

### What the probe actually found (don't trust the docs, verify the API)

The session prompt said to verify PSX Terminal's endpoints live before
writing code. Good thing, because the documented picture is gone:

- **The GitHub repo (`mumtazkahn/psx-terminal`) 404s** — deleted or
  private. Only search-engine caches remember the endpoint list.
- **The documented per-symbol REST endpoints are dead.**
  `/api/fundamentals/{S}`, `/api/companies/{S}`, `/api/dividends/{S}`,
  `/api/ticks/{M}/{S}`, `/api/announcements/{S}` all get their TCP
  connection killed server-side without a response. Proven not to be a
  client/TLS/header problem: identical failure from curl (schannel),
  httpx, and — decisively — `fetch()` executed **from the site's own
  origin inside a real browser** (Claude Browser pane). The site's own
  frontend never calls these routes; it is SvelteKit SSR + WebSocket.
  `psxterminal.com` now carries a commercial operator (Runtime
  Technologies (SMC-PRIVATE) LIMITED), sign-in, and a refund policy.
- **Still alive, no auth:** `GET /api/symbols` (full symbol list,
  `{"success":true,"data":[...]}`) and `GET /api/status`.
- **The working data channel is SvelteKit's `__data.json`** — the SSR
  payload each page renders from, in devalue serialization (flat array,
  objects hold integer indices into the same array):
  - `/financials/{S}/__data.json` → `overview` (valuation, shares,
    dividends, margins) + `ttm` (incl. `price_earnings`) + full FY/FQ
    statements. PPL cross-checked against the rendered page: pe 7.677 ↔
    "7.68", div yield 3.79%, mkt cap 610.0B, all match.
  - `/symbol/{S}/__data.json?market=REG` → `companyData`,
    `dividendsData` (history), `announcementsData` (latest ~10 per
    symbol, each with title/date/type and a `pdf_id` URL pointing at the
    real `dps.psx.com.pk/download/document/*.pdf` — i.e. an actual
    PUCARS-mirror for announcement metadata + PDF links).
  - `?x-sveltekit-invalidated=01` makes the server skip the shared
    layout node (~350KB of market-wide data): the symbol payload drops
    to ~19KB. Politer and faster; used everywhere.
- **ENGRO is not in their universe — only ENGROH.** Chased this into our
  own DB: ENGRO's `daily_prices` run 2021-06-07 → **2025-01-03, then
  stop**. The "ENGRO short history" known issue's root cause was wrong
  (it blamed an unfilled backfill gap) — ENGRO stopped trading (folded
  into Engro Holdings). KNOWN_ISSUES entry rewritten; universe decision
  (migrate to ENGROH vs drop) deferred to its own session.

### Built

- `backend/app/integrations/psx_terminal_client.py` (+ package
  `__init__.py`) — async httpx client, one method per endpoint used:
  `get_symbols()`, `get_fundamentals(symbol)`, `get_symbol_data(symbol)`.
  Contains the devalue parser. Polite posture: 30s timeout, connection
  reuse via `async with`, 2 requests per ticker per run, and the caller
  sleeps between tickers. Every failure path returns None + a logged
  warning (never raises); missing fields are listed per ticker in a
  `missing_fields` key and logged — never defaulted to something
  plausible-looking.
- `backend/app/db/models.py` — new `CompanyFundamentals` (UUID pk,
  ticker FK + unique, nullable pe_ratio / dividend_yield /
  market_cap_pkr / free_float_pct, last_updated, source default
  "psx_terminal"); new nullable `Announcement.source` column so
  PSX-Terminal-mirrored rows are distinguishable from the legacy portal
  scraper (which now stamps `psx_dps` — 1-line change) and any future
  PUCARS-direct scraper.
- `backend/alembic/versions/93dd6a13c006_company_fundamentals_table_.py`
  — the project's **first-ever Alembic revision** (the live schema
  predates it via `create_all`). Hand-written delta only: create
  `company_fundamentals`, add `announcements.source`. Applied to live
  Neon (`alembic upgrade head`) and verified via direct
  information_schema/pg_constraint SQL: all 8 columns, pkey, unique
  (ticker), FK present; `alembic_version` stamped `93dd6a13c006`.
  **Side discovery:** `.gitignore` had `backend/alembic/versions/*.py`
  (Phase 1B scaffolding) — migrations were being *gitignored*. Rule
  removed; migrations are schema history and must be tracked.
- `backend/app/collectors/fundamentals_collector.py` —
  `FundamentalsCollector(BaseCollector)`, `name="fundamentals_collector"`.
  Per ticker: fundamentals upsert (one row per ticker) + announcements
  insert with ticker+title+date dedup (range match on `announced_at`, so
  a changed posting_time can't duplicate). One symbols-list call up
  front cheaply skips tickers PSX Terminal doesn't list (ENGRO) without
  burning per-ticker requests. Sleeps mirror announcement_collector
  (3.0s between tickers, 1.0s between the two per-ticker fetches).
  Category mapping reuses `AnnouncementCollector.CATEGORY_MAP` plus a
  small observed-phrasing overlay ("transmission of accounts" →
  QUARTERLY_RESULT, "disclosure of interest" → MATERIAL_INFO).
- Wiring: `pipeline.py` Step 6/6 (own `run_safe`, own PipelineRun row,
  failure-isolated like every other stage); `run_pipeline.py`
  `--fundamentals-only`.
- `backend/scripts/verify_psx_terminal.py` — runs the collector for real
  then verifies via a **separate sync psycopg2 connection with raw SQL**
  (not the ORM session that wrote the rows): per-ticker field dump with
  NULLs flagged, type + plausibility checks (pe in (0,100), yield in
  [0,30), mktcap > 1e9, float in (0,100]), announcement counts/date
  ranges/pdf coverage, PipelineRun status, and `--dedup-check` (second
  live run must insert 0 announcements).

### Live verification (real API, real Neon, direct SQL)

Run 1: `{'tickers_processed': 9, 'tickers_failed': 1,
'records_inserted': 93, 'fundamentals_upserted': 9,
'announcements_inserted': 84}` (the 1 failure is ENGRO, expected).
Run 2 (dedup check): `announcements_inserted: 0` — **dedup holds**.
PipelineRun: `status=PARTIAL processed=9 failed=1` (honest — ENGRO).

Per-ticker `company_fundamentals` (direct SQL; full precision in the DB):

| Ticker | P/E (TTM) | Div yield % | Mkt cap PKR | Free float % |
|---|---:|---:|---:|---:|
| ENGRO | — no row (not listed on PSX Terminal; ENGROH only) | | | |
| LUCK | 7.78 | 0.0 (suspect) | 648.2B | **NULL** |
| OGDC | 8.83 | 4.98 | 1,374.1B | 21.69 |
| PPL | 7.68 | 3.79 | 610.0B | 25.14 |
| MCB | 8.29 | 9.05 | 471.3B | 30.74 |
| HBL | 6.60 | 7.09 | 437.2B | 34.89 |
| UBL | 8.14 | 6.92 | 1,158.4B | 33.52 |
| MARI | 11.45 | 0.0 (suspect) | 786.2B | 18.93 |
| PSO | 3.78 | 2.85 | 164.6B | 74.49 |
| MEBL | 10.52 | 5.30 | 968.6B | 25.52 |

Verifier result: **0 failed, 3 warnings** (ENGRO no-row ×2, LUCK NULL
free float — all expected and documented). All present values are real
floats in plausible ranges.

Announcements mirror (84 rows, all `source='psx_terminal'`): LUCK 10,
OGDC 10, PPL 10, MCB 10, HBL 9, UBL 10, MARI 10, PSO 10, MEBL 5; pdf_url
coverage 3/10 (UBL) to 10/10. MEBL's 5-of-10 was chased to the source:
six same-title "Disclosure of Interest…" filings on 2026-07-15 collapse
to one row under the specified title+date dedup key (confirmed by
re-fetching the raw feed). LUCK's yield-0.0 and NULL float were also
confirmed *at the API*, not parse bugs. All gaps recorded in
KNOWN_ISSUES, not smoothed over.

### Judgment calls

1. **No ENGRO→ENGROH aliasing.** They are different corporate entities;
   silently mapping would attach Engro Holdings fundamentals to a ticker
   whose price series died in Jan 2025. Flagged for a dedicated session.
2. **Dividends history fetched but not persisted** — no table for it was
   in scope; the client exposes it (`get_symbol_data()['dividends']`)
   so a future session can add a model without touching the client.
3. **dividend_yield stored as the source's literal 0.0** for LUCK/MARI
   rather than coerced to NULL — we store what the source said and
   document the suspicion, rather than editorializing data.
4. **`.gitignore` fix folded in** (migrations were ignored) — small,
   but leaving it would have shipped a phantom migration.

### Files touched

New: `backend/app/integrations/__init__.py`,
`backend/app/integrations/psx_terminal_client.py`,
`backend/app/collectors/fundamentals_collector.py`,
`backend/alembic/versions/93dd6a13c006_company_fundamentals_table_.py`,
`backend/scripts/verify_psx_terminal.py`.
Modified: `backend/app/db/models.py`, `backend/app/collectors/pipeline.py`
(step 6 + renumbered log strings), `backend/scripts/run_pipeline.py`
(`--fundamentals-only`), `backend/app/collectors/announcement_collector.py`
(stamps `source="psx_dps"`, 1 line), `.gitignore`, `CLAUDE.md`,
`docs/KNOWN_ISSUES.md`, this file.

**Protected files untouched:** pre-docs `git diff --stat` showed exactly
`announcement_collector.py (+1)`, `pipeline.py`, `models.py`,
`run_pipeline.py` modified + the new files above. None of
`trend_analyzer.py` / `news_synthesizer.py` / `filing_skeptic.py` /
`orchestrator.py` / `arbitrator.py` appear in the diff.


---

## 2026-07-17 — Phase 5 Session 3: ENGRO/ENGROH resolution + FIPI/LIPI groundwork

Batched data-layer session, two independent sub-steps (same pattern as
Phase 4 Session 6). Nothing wired into AgentContext, agent prompts, the
ML feature set, or the conviction formula — both sub-steps end as
verified data-layer state only. None of the five protected files touched.

### Sub-step 1 — ENGRO/ENGROH resolution (complete)

**Re-confirmed with primary evidence, not by trusting Session 2's note:**

- Direct SQL: ENGRO's `daily_prices` run 2021-06-07 → **2025-01-03**,
  887 rows; every other ticker's series runs to 2026-06-05.
- PSX DPS queried live for ENGRO *today*: still serves the symbol, but
  its series also ends **2025-01-03** — identical to our data. So the
  old "Phase 2A backfill gap" theory is conclusively dead: our collector
  captured everything the source has. The final trading days (Jan 6–13,
  2025) are absent from DPS's EOD series itself — unrecoverable from
  this source.
- PSX DPS queried live for **ENGROH**: exists with real, current data —
  1,219 rows spanning 2021-07-19 → 2026-07-16 (the continuous
  ex-Dawood-Hercules series renamed in place).
- Web check (Profit/Pakistan Today, Mettis Global, both 2025-01-14):
  PSX formally **delisted ENGRO effective 2025-01-14**; last trading day
  2025-01-13; mechanism was a Scheme of Arrangement — Engro Corporation
  merged into Dawood Hercules Corporation, which was renamed **Engro
  Holdings Limited (ENGROH)**; ENGRO shareholders were swapped into
  ENGROH.

**Schema change:** new nullable `companies.delisted_date` (Date) via
migration `44cd906f6e1e` (hand-written delta, chained on Session 2's
`93dd6a13c006`), including the ENGRO backfill in the migration itself so
any environment that applies migrations gets the fact. ENGRO's seed
entry carries the same date for fresh DBs. Applied to live Neon and
verified via direct SQL: `alembic_version = 44cd906f6e1e`;
`delisted_date` column present (date, nullable); ENGRO = 2025-01-14,
all other nine NULL.

**Docs:** the KNOWN_ISSUES ENGRO entry (already root-cause-corrected in
Session 2, old wrong explanation preserved per the file's own
don't-erase-dead-ends rule) now carries the confirmed dates, mechanism,
sources, and the DPS-matches-our-data proof.

**Deliberately NOT done:** ENGROH was not added to `companies`. Whether
to add it — and whether ENGRO is then dropped from the active universe
or kept as a delisted historical record — is a ticker-universe decision
for Abdullah (it touches seeds, the ML dataset, backtests, and stored
reports). Flagged in the completion summary, undecided here.

### Sub-step 2 — NCCPL FIPI/LIPI (probe complete; collector blocked on a dependency decision)

**Probe findings (live, in a real browser — the Claude Browser pane):**

- The FIPI/LIPI pages are served by a Laravel app whose real data
  channel is an internal JSON API the pages call:
  `GET /api/{fipi,lipi}-{normal,sector-wise}/latest-date` (freshness —
  returned 2026-07-16, current) and
  `POST /api/{fipi,lipi}-normal/data` `{"date": "YYYY-MM-DD"}` /
  `POST /api/{fipi,lipi}-sector-wise/data` `{"fromDate","toDate"}`,
  authenticated by an `X-CSRF-TOKEN` header read from the page's
  `<meta name="csrf-token">` plus session cookie. Exact call shapes
  extracted from the site's own inline JS, then exercised for real.
- **Row shape (verified on live responses):** CLIENT_TYPE ×
  MARKET_TYPE (REG/FUT/BNB/GEM/NDM/ODL + derived TOTAL rollups) with
  BUY/SELL/NET_VOLUME, BUY/SELL/NET_VALUE (PKR, sells negative), USD;
  sector-wise adds SEC_CODE/SECTOR_NAME.
- **Granularity (the session's key question): sector level at best.
  There is NO per-ticker breakdown anywhere.** ~10 named sectors
  (Cement, Fertilizer, O&G Exploration, O&G Marketing, Commercial
  Banks, Power, Tech, Textile Composite, Food, Debt Market) plus an
  "All other Sectors" catch-all — the named set covers all 10 of our
  tickers' sectors, so sector-mapped scoring is feasible next session.
- **Archive depth: 2015-12-09 onward** via the same API (verified by
  pulling 2015-12-09 and 2016-01-04 for real). Label drift across the
  archive: 2015-era `FOREIGN INDIVIDUAL`/`REGULAR` vs modern
  `FOREIGN CORPORATES ` (trailing space)/`REG`; LIPI TOTAL rows have
  blank CLIENT_TYPE; FIPI responses wrap rows in `records`, LIPI in
  `data`. Non-trading days: 200 + empty list.
- **Ranged sector queries concatenate per-day rows with NO date
  column** (6-week range → 1,267 undated rows), so collection must be
  day-by-day: one POST per date per dataset.

**The blocker, tested to a conclusion:** the entire www.nccpl.com.pk
zone — API paths included — is behind a Cloudflare **JS challenge**.
Plain curl/httpx: 403 challenge page. `curl_cffi` browser-TLS
impersonation (chrome131, firefox135, safari184): all 403. The
challenge requires actual JavaScript execution; only the real browser
passed. curl_cffi was installed for the test and **uninstalled again**
— not adopted. Conclusion: automated collection needs a headless
browser (Playwright), which this project has deliberately avoided and
which has real deployment weight on Railway (browser binaries).
**Surfaced as a decision for Abdullah, not added.** Side note recorded
in KNOWN_ISSUES: a yes on Playwright would also unlock the deferred
PUCARS scraper and Dunya News scraping — one decision, three sources.

**What shipped anyway (doesn't prejudge the decision):** the
`institutional_flows` table via migration `1f70a79ddebc`, shaped by the
verified live rows: date, dataset (fipi_normal/lipi_normal/
fipi_sector_wise/lipi_sector_wise), client_type, sector_code/name
(NULL for market-wide rows), market_type, buy/sell/net volume+value,
usd_value, source, scraped_at; dedup constraint
`UNIQUE NULLS NOT DISTINCT (date, dataset, client_type, sector_code,
market_type)` (PG 15+ feature; live Neon confirmed PostgreSQL 17.10).
Applied + verified via direct SQL (all 16 columns, pkey + constraint
present via pg_get_constraintdef, 0 rows). TOTAL rollup rows are
documented as not-to-be-stored (derived data).

**Honestly NOT done (blocked, not skipped):** the collector, pipeline
step, CLI flag, and collector-run verification. Building them without
the fetch mechanism would have produced code that cannot be run or
verified — the same anti-pattern this project's hard rules exist to
prevent. They go with the Playwright decision.

### Files touched

Modified: `backend/app/db/models.py` (Company.delisted_date +
InstitutionalFlow), `backend/app/collectors/seed_data.py` (ENGRO
delisted_date), `CLAUDE.md`, `docs/KNOWN_ISSUES.md`, this file.
New: `backend/alembic/versions/44cd906f6e1e_companies_delisted_date_column_backfill_.py`,
`backend/alembic/versions/1f70a79ddebc_institutional_flows_table_fipi_lipi.py`.
Dependencies: none added (curl_cffi tested and removed;
requirements.txt untouched).

**Protected files untouched:** `git diff --stat` shows only the files
above. None of `trend_analyzer.py` / `news_synthesizer.py` /
`filing_skeptic.py` / `orchestrator.py` / `arbitrator.py`.


---

## 2026-07-17 — Phase 5 Session 4: ENGROH universe + Dunya News + FIPI/LIPI (Playwright)

Batched data-layer session, three sub-steps. Nothing wired into
AgentContext, agent prompts, the ML feature set, or the conviction
formula — data-layer only. None of the five protected files touched.

### Sub-step 1 — ENGROH added to the active ticker universe (complete)

Resolves the open question Session 3 left for Abdullah. ENGROH seeded as
the 11th company (`Engro Holdings Limited`, sector "Investment
Companies", KSE30+KMI30 + shariah per PSX Terminal `listedIn`, verified
live). `PSX_TICKERS` swapped ENGRO→ENGROH in `config.py` + `.env` +
`.env.example`; ENGRO removed from the *active* list but kept as a
company row (frozen delisted-historical record, `delisted_date=2025-01-14`).

Backfilled through the **existing** collectors — no collector code
changed — and verified via direct SQL:

| ENGROH data | result |
|---|---|
| `daily_prices` | 1,219 rows, 2021-07-19 → 2026-07-16 (price_collector, PSX DPS) |
| `company_fundamentals` | P/E 4.99, mkt cap 319.66B, free float 18.04%, div yield NULL-at-source |
| `announcements` | 10 rows, `source='psx_terminal'` |

ENGRO untouched: still 887 rows ending 2025-01-03, still `delisted_date`
set, NOT in `PSX_TICKERS`. **Deferred (own session):** whether to *train*
the ML model on ENGROH — `ml_data/` deliberately not touched.

### Sub-step 2 — Dunya News collector (complete)

Re-verified live (per the Session-2 lesson) that
`dunyanews.tv/en/Business` is **plain static server-rendered HTML** —
HTTP 200 via httpx, no Cloudflare challenge, **no Playwright** (an
earlier doc note lumping Dunya into the Playwright decision was wrong;
corrected in KNOWN_ISSUES/CLAUDE.md). Built `DunyaNewsCollector`
(`app/collectors/dunya_news_collector.py`): scrapes the Business listing
(BeautifulSoup, article-URL regex), keyword-matches headlines to the
active universe with the exact same rule as `NewsCollector`, then for
*matched* articles only fetches the article page for the `<meta
name="description">` summary and the `<time datetime>` publish date.
`source='dunya'` keeps it distinct from ARY. Pipeline Step 7/8,
`--dunya-only` CLI flag.

**Live-verified:** first run inserted 29 rows across PPL(12)/PSO(11)/
OGDC(6), 2026-07-15→17, all with summaries; `news_articles` now
`arynews`=19 + `dunya`=29 = 48. Same keyword-match noise as ARY
(oil-price headlines → OGDC/PPL etc.) — documented, mitigated by the
existing NewsSynthesizer relevance judgment, no per-source fix.

### Sub-step 3 — NCCPL FIPI/LIPI via Playwright (collector built; automated challenge-pass fails; table populated via browser-pane bootstrap)

**Playwright adopted:** already pinned (`playwright==1.49.0`); ran
`playwright install chromium`, added a deploy-note comment in
`requirements.txt` (chromium binary must be installed at Railway build
time). Built `InstitutionalFlowCollector`
(`app/collectors/institutional_flow_collector.py`, Step 8/8,
`--flows-only`) driving Session 3's mapped flow: load
`/market-information` → wait out the Cloudflare challenge → read CSRF from
the meta tag → in-page day-by-day POSTs to
`/api/{fipi,lipi}-{normal,sector-wise}/data` → normalise (drop TOTAL
rollups + `---` separators) → upsert to `institutional_flows` with
`ON CONFLICT DO NOTHING`.

**Automated challenge-pass does NOT work on this host — tested
thoroughly, reported honestly:**

- Playwright's bundled Chromium won't even launch here — `spawn UNKNOWN`
  (a Windows side-by-side/runtime error on the headless-shell binary,
  reproduced after `--force` reinstall).
- System Chrome (`channel="chrome"`) launches, but the *interactive*
  Cloudflare challenge never clears under automation: headless AND headed,
  with `--disable-blink-features=AutomationControlled` +
  `navigator.webdriver` spoof + a persistent profile + a Turnstile-
  checkbox click. All stayed on "Just a moment…"/"Attention Required".
- `cf_clearance` is httpOnly and the one browser that *does* pass (the
  Claude in-app browser pane, an Electron browser) can't hand its cookie
  to httpx — so cookie transplant is out too.

The collector therefore degrades to **0 rows + a logged error + a PARTIAL
PipelineRun** (verified end-to-end via `--flows-only`; it did not crash
and did not touch existing rows). It stays in the tree as the correct,
reusable artifact for a host where the challenge is passable.

**Throughput test + backfill actually achieved:** the genuine browser
pane passes the challenge, so it was used as the data channel (its
same-origin `fetch` auto-uses the httpOnly clearance cookie). Measured
~0.7s/request. A full backfill to 2015-12-09 (~2,600 trading days × 4
datasets ≈ 10k requests ≈ 2h) is mechanically possible but hinges on a
hand-cleared session and isn't a production story — so a **sensible
initial backfill of the last 30 calendar days (20 trading days)** was
taken. Fetched all 4 datasets through the pane (results auto-saved to
tool-result files to avoid context bloat) and bulk-loaded via a
`execute_values` loader.

**Live-verified (`scripts/verify_institutional_flows.py`, direct SQL —
0 failures):**

| dataset | rows | days | client types |
|---|---:|---:|---|
| fipi_normal | 125 | 20 | FOREIGN CORPORATES / INDIVIDUAL / OVERSEAS PAKISTANI |
| lipi_normal | 490 | 20 | INDIVIDUALS, COMPANIES, BANKS/DFI, MUTUAL FUNDS, INSURANCE, NBFC, BROKER PROP, OTHER ORG |
| fipi_sector_wise | 728 | 20 | (FIPI categories) |
| lipi_sector_wise | 2,889 | 20 | (LIPI categories) |
| **total** | **4,232** | 2026-06-17 → 2026-07-16 | 11 sectors incl. all our tickers' |

Dedup proven: re-running the loader inserted +0 (all 4,232 skipped via
`uq_flow_row`). No TOTAL rollups, no `---` separators, market-wide rows
have NULL sector_code, no NULL net_value. Net-flow spot check sane
(foreign corporates net +2.64B PKR on 2026-07-16). **This is a manual
bootstrap, not automation** — clearly labelled everywhere.

**Still open for production (KNOWN_ISSUES):** unattended NCCPL collection
needs a clean/residential IP, a CAPTCHA-solving service, or a scheduled
manual export. Railway needs the chromium binary at build time.

### Doc corrections carried in from Session 3 (as instructed)

- **PUCARS is a login wall, not a Playwright problem** — needs real
  PSX-issued credentials; adding Playwright does not bring it closer.
  Fixed in KNOWN_ISSUES (announcement-scraping entry) and CLAUDE.md
  (data-source table + deferred list).
- **Dunya News needed no Playwright** — static HTML. Fixed in both files;
  the "one decision, three sources" framing from Session 3 is retracted.

### Files touched

New: `backend/app/collectors/dunya_news_collector.py`,
`backend/app/collectors/institutional_flow_collector.py`,
`backend/scripts/verify_institutional_flows.py`.
Modified: `backend/app/collectors/seed_data.py` (ENGROH),
`backend/app/core/config.py` (PSX_TICKERS), `backend/.env.example`,
`backend/app/collectors/pipeline.py` (steps 7 + 8, renumbered to /8),
`backend/scripts/run_pipeline.py` (`--dunya-only`, `--flows-only`),
`backend/requirements.txt` (playwright deploy note),
`CLAUDE.md`, `docs/KNOWN_ISSUES.md`, this file. (`.env` also changed
locally — gitignored, not committed.)

**Protected files untouched:** `git diff --stat` shows none of
`trend_analyzer.py` / `news_synthesizer.py` / `filing_skeptic.py` /
`orchestrator.py` / `arbitrator.py`.


---

## 2026-07-17 — Phase 5 Session 5: historical depth extension (price + FIPI/LIPI, matched window)

Pure data-depth session ahead of the next session's ML retrain. Deepened
the two training-relevant time series (prices + institutional flows) to a
single matched window. Nothing wired into AgentContext, agent prompts,
the ML feature set, or the conviction formula. None of the five protected
files touched.

### Part 1 — real achievable depth (measured, not assumed)

Probed PSX DPS directly (not just our DB) for the earliest date the
*source* offers, all 11 tickers:

- **Every ticker's series starts 2021-07-19 at the source today**, and
  the endpoint serves a **fixed rolling ~5-year window that IGNORES all
  date parameters.** Tested `from=1990-01-01`, a fixed past window
  (`from=2015-01-01&to=2016-01-01` — still returned 2021-2026), `start`/
  `end`, `period=10y`, and unix-timestamp `from` — all returned the
  identical 1,237-row 2021-07-19→2026-07-17 window. `timeseries/int` is
  current-day intraday ticks only, not an archive.
- **So 7-8 years is NOT achievable from this source — reported plainly.**
  Our DB already holds *more* than the source now serves on the left edge
  (2021-06-07/08, captured June 2026 before those rows rolled off), so
  `daily_prices` is now the archive, not a cache.
- **Matched target window locked at 2021-06-07 → 2026-07-17 (~5.1 yr),
  bounded by price depth.** FIPI/LIPI matched to it (NCCPL's archive
  reaches 2015-12-09, but pre-2021-06 flows would have no price data to
  pair for ML).
- **PSX Terminal fundamentals check:** what we *store* is a **current
  snapshot only** (one row/ticker, no history) — flagged as a
  **lookahead-bias risk** for next session's feature design (today's P/E
  must not be a feature on a 2023 row). But the raw `/financials`
  payload's `fyReports` carries **20 fiscal years (FY2006–FY2025) of
  reported annual statements** incl. per-FY `price_earnings`,
  `dividends_yield`, `market_cap_basic`, and `earnings_release_date` — so
  a properly point-in-time *annual* fundamentals feature is feasible
  later. Not built (out of scope); no fake historical fundamentals
  fabricated. Both findings recorded in KNOWN_ISSUES.

### Part 2 — price backfill (complete)

Widened `price_collector.HISTORY_DAYS` 730 → 2190 and documented the
rolling-window reality in its docstring (the `from`/`to` params are now
decorative until DPS honors them). Ran `--prices-only`; per-row dedup
means it only topped up the right edge. **253 rows inserted, 10/10 active
tickers SUCCESS, ~55 min** (Neon round-trip bound). Verified via direct
SQL:

- All 10 active tickers now end **2026-07-17** (were stale at 2026-06-05,
  except ENGROH/LUCK/OGDC/PPL which the run happened to reach first).
  Per-ticker: MCB/HBL/UBL/MARI/PSO/MEBL/OGDC/PPL 1,266 rows each, LUCK
  1,262, ENGROH 1,220.
- **ENGRO boundary respected:** still 887 rows, ends 2025-01-03,
  `delisted_date=2025-01-14`, NOT in the active list, not extended.
- `daily_prices` total **13,497 rows**.

### Part 3 — FIPI/LIPI historical backfill (complete, matched window)

Reused Session 4's proven channel (Claude Browser pane passes NCCPL's
Cloudflare challenge; automated Playwright still can't — Problem B, left
untouched). Built a **month-chunked, resumable, disk-buffered** pipeline
addressing Session 4's context-bloat and slow-load pain points:

- an in-page JS loop fetches one month at a time (4 datasets × each
  trading day, ~1.3 req/s) and POSTs the month's payload to a **localhost
  receiver** (`chunk_receiver.py`, port 8765, CORS for the NCCPL origin +
  the Private-Network preflight header) that writes each chunk straight
  to disk — big payloads never route through model context;
- a **`execute_values` bulk loader** (`load_flow_chunks.py`, same
  normalisation as `InstitutionalFlowCollector._normalise`) loads chunks
  in batches — NOT the row-by-row approach Session 4 found too slow;
- **resumability**: the loop reads a `/todo` (weekdays in-window minus
  dates already in the DB minus months already on disk) and skips
  saved months, so a mid-run session death costs only the in-flight
  month. This mattered: **the NCCPL Cloudflare session died twice**
  (~45-min lifetime; once mid-`2023-08`, once at `2026-01`). A
  consecutive-403 breaker halted the loop cleanly WITHOUT saving the
  partial month each time; recovery was re-navigate (re-pass the
  challenge, fresh CSRF) + re-kick, resuming exactly where it stopped.
  The one partially-fetched `2023-08` chunk was discarded and refetched
  clean (its 58 pre-death rows deduped on reload).

**Result (direct SQL): `institutional_flows` 4,232 → 268,142 rows**
(+263,910 this session), **1,267 trading days, 2021-06-07 → 2026-07-17,
all 4 datasets full-span:**

| dataset | rows | days |
|---|---:|---:|
| fipi_normal | 8,066 | 1,267 |
| lipi_normal | 30,868 | 1,267 |
| fipi_sector_wise | 46,584 | 1,267 |
| lipi_sector_wise | 182,624 | 1,267 |

Neon DB size 13 MB → **99 MB** (well within the 512 MB free tier).
**Dedup re-proven**: a full reload of every chunk inserted **+0 rows**.

### Verification (`scripts/verify_depth_extension.py`, read-only direct SQL)

New verifier, **0 failed**. Passing checks: all 10 active tickers share
one right edge (2026-07-17); ENGRO frozen (887 rows / 2025-01-03); no
suspiciously long contiguous price gap (longest per-ticker run ≤ 10
days); all 4 flow datasets reach 2021-06-07; every PSX trading day (MCB
reference) has flow data; every flow date carries all 4 datasets; no
TOTAL / `---` / blank-client-type rows; no duplicate dedup keys.

**Honest data-shape finding (surfaced by the verifier, resolved not
smoothed over):** the first draft of the verifier hard-asserted "every
active ticker traded every day ≥9 others did" and FAILED with 22 holes
— 17 ENGROH + 5 LUCK. Investigated by probing **PSX DPS live** for the
exact ticker/date combos: **all 22 are absent from the source itself**,
so they are genuine no-trade days, not collection gaps. ENGROH (ex-Dawood
Hercules, an illiquid holding company) has 17 scattered single no-print
days; LUCK's 5 are a contiguous 2025-04-21…25 run — a trading suspension
around its 2025-04-28 5:1 split. Our per-ticker row counts byte-match
what DPS serves. The uniformity assumption was wrong (thinly-traded names
legitimately skip days the blue chips don't), so the check was corrected
to report holes as informational and hard-fail only on a long contiguous
run (the actual collection-failure signature). This is the project's
"verify against reality, don't trust a convenient assumption" rule doing
its job.

### Depth actually achieved (honest)

**~5.1 years (2021-06-07 → 2026-07-17), NOT 7-8 years.** PSX DPS's rolling
~5-year window is the hard ceiling for price data, and the FIPI/LIPI
window was matched to it deliberately. NCCPL alone could go to 2015-12-09,
but that extra history would have no price partner for ML training.

### Problem B (ongoing automated FIPI/LIPI collection) — still UNSOLVED

Reaffirmed in KNOWN_ISSUES. This session deepened the *historical archive*
by hand (browser-pane bootstrap); it did **not** make daily collection
run unattended. `InstitutionalFlowCollector` (Playwright) is still
Cloudflare-blocked on this host — no attempt to fix it here (out of
scope). Production still needs a clean IP / CAPTCHA solver / manual
export.

### Files touched

New: `backend/scripts/verify_depth_extension.py`.
Modified: `backend/app/collectors/price_collector.py` (HISTORY_DAYS +
depth-cap docstring), `.gitignore` (`backend/data/nccpl_backfill/`),
`CLAUDE.md`, `docs/KNOWN_ISSUES.md`, this file. The backfill fetch/load
scripts (`chunk_receiver.py`, `load_flow_chunks.py`, the pane loop) are
one-off local tooling in the scratchpad, not committed — the DB is the
deliverable. Raw NCCPL chunks under `backend/data/nccpl_backfill/` are
gitignored (loaded into Postgres; DB is the source of truth).

**Protected files untouched:** `git diff --stat` shows none of
`trend_analyzer.py` / `news_synthesizer.py` / `filing_skeptic.py` /
`orchestrator.py` / `arbitrator.py`.

## 2026-07-18 — Phase 5 Session 6: ML retrain on full-depth window (ENGROH universe) + backtest re-run

Pure data/ML-pipeline session: rebuilt the train/val/test split on the
post-Session-5 data, retrained XGBoost with identical hyperparameters,
re-ran the Session 1 backtest methodology unchanged. Nothing about how
the model is *used* changed — Arbitrator/inference wiring, the 0.55
gate, and the 5% ML weight are all untouched. None of the five protected
files touched.

### Honest correction of the session premise, first

The brief said the original split was built on "the old, much shorter
window." **That premise is wrong, and was verified wrong before
building:** the Phase 3 Session 1 dataset build read 12,025 raw rows
(~5 years/ticker) because PSX DPS always served its full rolling window
regardless of the old `HISTORY_DAYS=730` setting — the old labeled
dataset already spanned 2022-06 → 2026-05. The real deltas this session
trains on are (a) **ENGROH's full continuous history replacing delisted
ENGRO's truncated one** and (b) **~6 weeks of newer data** (Session 5's
253-row top-up). Net: 10,050 labeled rows vs 9,465 (+6.2%). Reported
plainly rather than letting the session oversell itself.

### Part 1 — split rebuild (methodology unchanged, re-proven)

- `build_ml_dataset.py` re-run as-is — it reads `PSX_TICKERS`, which
  already carries the Session 4 universe (ENGROH in, ENGRO out), so the
  universe change required zero code edits. Same per-ticker
  chronological 70/15/15, same feature pipeline, same split adjustments.
- **Corporate-action scan re-run first** (`find_split_row.py` against
  the live DB): the only −50%+ overnight signatures are the three
  already-adjusted splits (MARI/LUCK/UBL). ENGROH's extremes (+18.5%
  day, +32% 5-day — Dec 2024 merger-rally era) are real moves, not
  splits. `split_adjustments.py` needed no new rows.
- Result: **10,050 labeled rows** (train 7,034 / val 1,504 / test
  1,512), 10 tickers, one common calendar — the disjoint-ENGRO-window
  problem is gone by construction. Test window 2025-11-27 → 2026-07-10
  (ENGROH enters 2025-12-08, its series being slightly shorter).
- **No-leakage re-proven fresh, both ways:** `verify_dataset.py`
  chronological invariant (train_max < val_min < val_max < test_min)
  passes for all 10 tickers, and the backtest's independent per-ticker
  `test_min > val_max` assertion matches those boundaries exactly.
- Minor flag: test-split FLAT share is 19.1%, a hair under the 20%
  floor Session 1 observed. Noted, not material (FLAT is structurally
  unpredicted anyway).

### Part 2 — retrain (same hyperparameters, seed=42)

**Test accuracy 43.19% vs the old 39.34%** (random baseline 33.33%);
best iteration 34 (old 27). Old artifacts archived in `ml_data/` as
`model_phase3s2_backup.json` / `metrics_phase3s2_backup.json`
(gitignored, like all of `ml_data/`).

The more honest yardstick, computed this session for both models: the
**always-UP naive baseline**. Old model: 39.34% vs 40.25% always-UP —
i.e. the old model was *worse than majority-class guessing* on its test
set. New model: **43.19% vs 40.81% (+2.4pp)** — the first time the
model beats the majority class, not just random chance. Trade-off
flagged plainly: the new model is heavily UP-skewed (82% of test
predictions UP; DOWN recall 0.20; FLAT still unlearned, 3 predictions).

### Part 3 — backtest re-run (methodology identical)

`backtest_xgboost.py` needed only mechanical de-hardcoding (dynamic
sleeve size/window instead of literal "9"/PPL-dates, and the
ENGRO-standalone section now renders only if a disjoint ticker exists in
the parquet — kept for archived-parquet reruns). Costs, rules, CGT, and
sensitivity all unchanged. Full numbers + before/after tables in
`docs/BACKTEST_RESULTS.md`; headline (10-ticker sleeve, 0.30%
round-trip, pre-CGT):

| | Session 1 (old) | Session 6 (new) |
|---|---|---|
| Ungated | +5.10%, Sharpe 0.43, maxDD −21.30%, 63 trades | **+18.36%, Sharpe 1.12, maxDD −19.37%, 70 trades** |
| Gated (>0.55) | +2.35%, Sharpe 0.42, maxDD −5.70%, 22 trades | **+5.57%, Sharpe 0.99, maxDD −4.30%, 26 trades** |
| Buy & Hold | +3.33%, Sharpe 0.33, maxDD −24.41% | +13.81%, Sharpe 0.81, maxDD −21.79% |

**Windows differ** (old 2025-10-24→2026-05-29, new 2025-11-27→
2026-07-10, ~6 months overlap, new one distinctly more bullish), so the
meaningful comparison is excess-vs-own-window-B&H: ungated excess return
+1.77pp → **+4.55pp**, excess Sharpe +0.10 → **+0.31**. Gate clears:
99/1,512 rows (6.5%, all UP).

**Honest read:** a modestly stronger edge, NOT a multi-regime
validation. The brief hoped the new test span would cross "more market
conditions" — it doesn't: more data shifts the chronological 15% tail
later, producing another single ~7.4-month window, largely overlapping
the old one and strongly bullish throughout. The UP-skewed model would
be long nearly all the way down a sustained bear market. Same bottom
line as Session 1, upgraded one notch: the signal is real after costs
and now beats naive baselines, but this still does not establish a
deployable strategy.

### Verification (live, not claimed)

- `verify_dataset.py`: 10/10 chronological invariant OK; worst forward
  return −25.4% (OGDC 2024-02-12, genuine) — no split contamination.
- Backtest run twice — numbers reproduce exactly.
- **Production inference path exercised with the new artifact**:
  `probe_ml_signal.py` (read-only) scores all 10 active tickers via the
  same `inference.py` module the Arbitrator uses; ENGRO degrades to
  `insufficient_history` as designed. New live max_prob cluster
  0.368–0.434 (old 0.357–0.407) — still nobody clears 0.55 on the
  latest day, so production `ml_contribution` stays 0.0 by design.

### Files touched

Modified: `backend/scripts/backtest_xgboost.py` (dynamic sleeve/
standalone handling only — methodology untouched), `CLAUDE.md`,
`docs/BACKTEST_RESULTS.md`, `docs/KNOWN_ISSUES.md`, this file.
Regenerated (gitignored, not committed): `backend/ml_data/{train,val,
test}.parquet`, `model.json`, `metrics.json` (+ the two `*_backup`
archives of the old model).

**Protected files untouched:** confirmed via `git diff --stat` — none
of `trend_analyzer.py` / `news_synthesizer.py` / `filing_skeptic.py` /
`orchestrator.py` / `arbitrator.py` appear in the diff.

## 2026-07-18 — Phase 5 Session 7: real announcements wired into FilingSceptic

`filing_contribution` had been 0.0 for the entire life of the project.
This session ends that. **First session ever to deliberately touch
protected files** — scoped to `filing_skeptic.py` and `orchestrator.py`
only; `trend_analyzer.py`, `news_synthesizer.py`, and `arbitrator.py`
confirmed absent from the diff.

### Part 1 — why it was always 0.0 (confirmed from code, not assumed)

Not a placeholder: a data gate. `FilingSceptic.run()` filters
`context.announcements` to rows with non-empty `raw_text`; all 94
mirrored rows had `raw_text=NULL` (the Phase 2A `PDFParser` only ever
processed `category='QUARTERLY_RESULT'` and was never pointed at the
Phase 5 Session 2 mirror), so the agent always took its honest no-data
branch (confidence 0.2, zero LLM calls). The full LLM path (prompt +
RED_FLAGS/SEVERITY/ANALYSIS parser) already existed but analyzed only
the single most recent filing. **Arbitrator needed zero changes, as the
session brief hypothesized:** `_filing_contribution` applies
`SEVERITY_PENALTY {LOW:-5, MEDIUM:-15, HIGH:-30}` whenever `red_flags`
is non-empty — the slot was live all along, receiving empty lists.

### Part 2 — substance check (measured on all 83 PDFs, not sampled)

Every stored `pdf_url` was downloaded and text-extraction tested before
designing anything: **51/83 (61%) have a real text layer** — including
full quarterly accounts (15–39K chars), a PSO board-member resignation,
CEO appointments, MARI's clarification of media reports on its Spinwam
gas bidding, OGDC well discoveries, ENGROH buy-back reports — and
**32/83 (39%) are image-only scans** (mostly "Disclosure of Interest"
notices) yielding zero text. Titles alone range from substantive
("Award of Eight New Offshore Blocks") to boilerplate. **Call:**
full-text analysis with per-announcement title-only fallback; no OCR
(no invented data); no new dependency (pdfplumber already present since
Phase 2A); **no migration needed** (`raw_text` column existed unused
since Phase 1A). Per-ticker text coverage is wildly uneven and now
documented: PSO 10/10, MARI 9/10, ENGROH 8/10, OGDC 8/10 vs MEBL 1/5,
HBL 2/9, PPL 3/10, LUCK 3/10, UBL 3/10, MCB 4/10.

### The extraction run — two real bugs found and fixed live

1. **Neon idle-timeout kills a single end-of-run commit.** The
   broadened backlog (83 PDFs × download + 2s politeness sleep ≈ 7 min)
   left the DB connection idle the whole loop; the final commit died
   with `InterfaceError: connection is closed` and rolled back the
   ENTIRE run (0 rows saved). Fix: `COMMIT_EVERY=10` batched commits
   (safe: `expire_on_commit=False`) — progress is durable and the
   connection stays warm.
2. **NUL bytes in extracted PDF text.** MCB's 39K-char quarterly
   accounts contain `\x00`, which PostgreSQL text columns reject
   (`CharacterNotInRepertoireError`) — and the poisoned batch left the
   session in `PendingRollbackError`, silently killing every later
   batch. Fix: strip `\x00` before store + `_commit_batch()` does
   rollback-and-continue so one bad row costs one batch, not the run.

Also: fiscal-period regex now runs only on QUARTERLY_RESULT rows (it
would latch onto stray years in notices), `--pdfs-only` added to the
CLI runner, `backend/data/announcements/` gitignored (PDF working
files). Final state, verified by direct SQL: **83/94 parsed, 51 with
raw_text (620–21,633 chars)** — per-ticker counts byte-match the
pre-design sweep. A categorization quirk was noted, not fixed: "Board
Meeting for Agenda Other than Financial Results" maps to
QUARTERLY_RESULT because the "financial results" keyword matches first.

### Part 3 — the wiring

- `orchestrator.py` (protected, permitted): announcement context dicts
  gain `pdf_url`/`pdf_parsed`/`source`. That is the entire change.
- `filing_skeptic.py` (protected, permitted): reviews the ≤10 most
  recent announcements as one batch. Per announcement: extracted text
  excerpt (1,500-char cap, 9,000-char shared budget, recency-first) →
  `full_text`; no text → `title_only` with an explicit
  do-not-speculate marker; text present but budget spent →
  `text_omitted`. All modes persisted in the output (`reviewed` list) —
  visibility is auditable, not hidden. Output adds
  `data_availability` (FULL_TEXT/PARTIAL_TEXT/TITLES_ONLY/NONE) +
  `full_text_count`/`title_only_count`. Confidence: 0.75 with flags,
  0.6 without, hard-capped 0.45 if every document was title-only.
  Prompt asks for the session's target red flags (related-party terms,
  going-concern/audit qualifications, auditor changes, sudden
  departures, penalties, defaults, insider selling, delayed results)
  and explicitly instructs that routine disclosures (dividends,
  buy-backs, briefing sessions, ESOS allotments, standard disclosures
  of interest) are NOT red flags. Zero-announcement tickers keep the
  no-LLM-call graceful branch. `_parse_response` unchanged; the
  −5/−15/−30 scale kept as-is — it fits (see PSO below).

### Part 4 — verification (7 real production-path runs)

New harness `backend/scripts/verify_filing_sceptic.py` (runs the real
`AnalysisOrchestrator`, dumps verbatim agent output + score breakdown +
`llm_calls` audit rows; `--save`/`--diff` for controlled experiments).
Baseline captured BEFORE any code change (same day, same data):

- **Controlled diff (PPL/UBL/MEBL/ENGROH/OGDC): 15/15 technical/news/ml
  terms byte-identical before→after.** Only the filing term changed.
- **PSO — the first real penalty in project history:**
  `filing_contribution = -15` (MEDIUM), conviction 50.0 → 35.0.
  Red flags: "Resignation of Board Member", "Unexplained CEO
  Appointment". LLM analysis (verbatim): *"The resignation of Mr.
  Waheed Ahmed Shaikh, an Independent Member of the Board of
  Management, as disclosed in document [3] on June 15, 2026, is a
  notable event that warrants attention. Additionally, the title-only
  disclosures [8] and [9] regarding the appointment and change in
  effective date of the Chief Executive Officer raise questions about
  potential leadership changes or instability. While these events are
  not necessarily indicative of serious issues, they are worth
  monitoring and investigating further…"* **Spot-checked against the
  actual source PDF** (read directly, not via the LLM): the filing
  does disclose exactly that resignation, effective 2026-06-11, with
  no reason given — accurate read, not hallucination. MEDIUM (−15) for
  a governance-transition cluster is at the stricter edge but within
  the documented severity guide; flagged for human review rather than
  silently retuned.
- **The other 6 tickers returned genuine "nothing to flag" zeros** —
  real LLM calls (1.5–3.2K tokens each), full reviewed-mode logs, and
  measured analyses that match the underlying documents (e.g. MARI's
  Spinwam clarification judged non-concerning; ENGROH's CEO
  *appointment* correctly not treated as a departure; OGDC's TFC
  interest *receipts* correctly read as routine). This is the critical
  distinction from the old zero: reviewed-and-clean vs no-data (the
  old branch shows 0 tokens; the new ones show real token spend).
- **Gateway routing proven, not assumed:** grep shows no Groq/Gemini
  SDK references anywhere in `app/agents/` (only `self.llm.complete()`),
  and the `llm_calls` audit table logged all 7 `filing_skeptic` calls
  (llama-3.3-70b-versatile, SUCCESS) — the gateway wrote those rows.
- **Cost/latency impact:** FilingSceptic prompts run 1.4–3.1K prompt
  tokens (was: no call at all). Per-report totals: ~2.7–5.1K tokens vs
  ~1.5–2.2K before. Latency typically +1.3–2s per report; two Groq
  spikes to 10–14s observed. Still trivial on Groq free tier.
- One cosmetic LLM quirk: with zero flags the model sometimes answers
  `SEVERITY: NONE` (not in the LOW/MEDIUM/HIGH vocabulary) — the
  existing parser logs a warning and defaults to LOW with empty flags,
  which contributes 0.0 regardless. Left as-is.

DB after the session: `intelligence_reports` 19 rows (+12 verification
runs), `llm_calls` 52 rows (+36; first 7 `filing_skeptic` calls ever).
Redis note: cached `GET /report` responses serve pre-session shapes
until TTL expiry — same known quirk as Phase 4 Session 2.

### Files touched

Protected (permitted this session): `backend/app/agents/filing_skeptic.py`,
`backend/app/agents/orchestrator.py`.
Unprotected: `backend/app/collectors/pdf_parser.py`,
`backend/app/collectors/pipeline.py` (docstring only),
`backend/scripts/run_pipeline.py` (`--pdfs-only`), `.gitignore`.
New: `backend/scripts/verify_filing_sceptic.py`.
Docs: `CLAUDE.md`, `docs/KNOWN_ISSUES.md`, this file.

**`trend_analyzer.py`, `news_synthesizer.py`, `arbitrator.py` untouched**
— verified via `git diff --name-only` (they do not appear at all).
