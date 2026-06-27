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

<!-- Next entry goes here. Add a new ## dated heading below this line. -->