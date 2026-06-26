# PSX Sentinel — Project Context for Claude

> **New session? Read in this order:** this file, then `docs/KNOWN_ISSUES.md`, 
> then the last 3-4 entries of `docs/BUILD_LOG.md`. Do not assume anything 
> works until you've checked the live database yourself — a "complete" status 
> from a previous session or chat history is not verification. Run the actual 
> query. This project has already been burned once by trusting an AI's claim 
> of success without checking the database directly (Phase 2A, May 30).

## What this is

Enterprise-grade AI financial intelligence platform for the Pakistan Stock 
Exchange (PSX). Combines an ML price-direction prediction model with a 
4-agent autonomous research pipeline. (Originally scoped as an earnings 
surprise model — rejected in Phase 3 Session 1, 2026-06-27, since the 
project has no reported-EPS/consensus-estimate data source; see Build Log.) 
This is a portfolio and career-leverage project for a final-year Data 
Science student — it must read as production quality to recruiters and 
technical interviewers, not as a student project.

Name: **PSX Sentinel**. Repository: `github.com/AbdullahRizwan-ML/psx-sentinel`

## Architecture (current, as built)

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL (Neon Cloud, free tier) + Redis (Upstash, free tier)
- **Agents:** Custom Python orchestration ONLY — no CrewAI, no LangChain. This was a deliberate reversal from an earlier plan (see Build Log, May 28).
- **LLM routing:** Groq (Llama 3.3-70B versatile) as primary, Gemini 2.0 Flash as fallback. All calls go through a single `LLMGateway` class — this is non-negotiable, see Hard Rules below.
- **Queue:** Celery + Redis (Upstash) for background/nightly jobs
- **Auth:** JWT (access + refresh tokens), bcrypt password hashing
- **Frontend:** Not built yet. Planned: Next.js 15 + TypeScript + Tailwind + shadcn/ui. Explicitly NOT Streamlit — this was rejected early on as looking like a student project.
- **Deployment targets (not yet deployed):** Vercel (frontend), Railway (backend), Neon Cloud (already in use for DB), Upstash (already in use for Redis)

## Hard rules — do not violate these in any future session

1. **Every LLM call goes through `app/core/llm_gateway.py` → `LLMGateway.complete()`.** No agent or collector ever calls the Groq or Gemini SDK directly. This is the single most important architectural rule in the project — it's what gives us cost tracking, circuit breakers, and audit logging for free.
2. **No CrewAI. No LangChain. No Streamlit.** Custom Python orchestration only. This was decided after reading production-engineer feedback (Reddit threads, May 28) showing these frameworks are too heavy/opaque for real deployments.
3. **Everything async.** No blocking I/O in request paths. Sync libraries (yfinance — now removed, feedparser, pdfplumber, playwright) must run via `asyncio.to_thread()`.
4. **Every collector and agent fails gracefully per-item.** One ticker failing must never crash the whole pipeline run. This is enforced via `BaseCollector.run_safe()` and `BaseAgent.run_safe()` — both wrap the actual logic in try/except and always return a result, never raise upward.
5. **Agents skip the LLM call entirely when there's no real data to analyze.** E.g., `NewsSynthesizer` returns a low-confidence result without calling the LLM if there are zero matched articles, rather than hallucinating an analysis from nothing. Same for `FilingSceptic` when no announcement text exists yet.
6. **Data source status (do not re-attempt dead sources without reading KNOWN_ISSUES.md first):**
   - ✅ **PSX DPS timeseries** (`dps.psx.com.pk/timeseries/eod/{ticker}`) — confirmed working, no auth, primary price source.
   - ✅ **ARY News RSS** (`arynews.tv/category/business/feed/`) — confirmed working, primary news source.
   - ❌ **yfinance** — permanently dead. Yahoo Finance blocks PSX `.KA` tickers via Cloudflare. Removed from requirements.txt. Do not reintroduce.
   - ❌ **Dawn Business RSS, Business Recorder RSS** — Cloudflare-blocked (403). Removed.
   - ❌ **PSX announcements JSON/HTML scraping** — PSX portal is JavaScript-rendered; static scraping returns nothing. Deferred to a future Playwright-based PUCARS scraper (not yet built).
   - ⚠️ **CapitalStake API** — looked into as a fundamentals source. No self-serve free tier (consultation-based commercial pricing only). Not pursued. Revisit only if/when productizing commercially with paying subscribers.

## Current build state

- [x] **Phase 1A** — Core infrastructure: config, 10 SQLAlchemy models, JWT security, async Redis client with circuit breaker, `LLMGateway` with Groq→Gemini failover, `BaseAgent` abstract class.
- [x] **Phase 1B** — Application layer: 22 FastAPI endpoints (auth, companies, intelligence, alerts, watchlist, health), Pydantic schemas, Celery scaffolding, Alembic setup, full JWT auth flow verified live.
- [x] **Phase 2A** — Data collectors: `BaseCollector` pattern, company seed data (10 KSE-30 tickers), price collector (PSX DPS), announcement collector (currently returns 0 — portal is JS-rendered), news collector (ARY News RSS), PDF parser (idle — nothing to parse yet), pipeline orchestrator, standalone CLI runner script. **Fixed and verified** after initial yfinance/RSS failures (see Build Log, May 30 – June 8).
- [x] **Phase 2B Session 1** — Four specialist agents built: `TrendAnalyzer`, `NewsSynthesizer`, `FilingSceptic`, `Arbitrator` (`backend/app/agents/`). All inherit `BaseAgent` correctly, all LLM calls go through `self.llm.complete()`, syntax-verified via `ast.parse()`. **Not yet verified end-to-end** — no orchestrator exists yet to actually run them against live data, so behavior at runtime is unconfirmed.
- [x] **Phase 2B Session 2** — `AnalysisOrchestrator` (`backend/app/agents/orchestrator.py`) wires the four agents together: builds `AgentContext` from the live database, runs `TrendAnalyzer`/`NewsSynthesizer`/`FilingSceptic` via `run_safe()`, feeds their output into `Arbitrator`, persists a real `IntelligenceReport`. Wired into both `POST /companies/{ticker}/analyze` (now runs inline and returns the saved report directly) and the Celery `run_analysis` task. **Live-verified** against 2 real tickers (PPL, MCB) — confirmed via direct SQL against `intelligence_reports` and `llm_calls` (not log output): 2 new report rows, 5 new `llm_calls` rows across Groq llama-3.3-70b-versatile, correct LLM skips for `FilingSceptic` (both tickers, 0 calls) and `NewsSynthesizer` (MCB only, 0 articles). None of the four agent files were modified during this session. See Build Log, 2026-06-23 (Session 2 entry) for full detail.
- [x] **Phase 3 Session 1** — ML feature pipeline + labeled dataset (no training yet). Target redefined from earnings surprise to 5-trading-day-ahead price direction (UP/DOWN/FLAT, ±1% flat band) — no EPS/consensus data exists in this project, and the target uses only the already-confirmed-live `daily_prices` data. `backend/app/ml/features.py` computes MA20/MA50/RSI(14)/momentum/volume-trend/volatility/52w-range-position per ticker; `backend/scripts/build_ml_dataset.py` pulls all 10 tickers' price history from the live DB, builds features, and writes a per-ticker chronological 70/15/15 train/val/test split to `backend/ml_data/*.parquet` (gitignored — local training artifact, not application state, not in Postgres). **Live-verified:** 12,025 raw `daily_prices` rows read, 2,560 dropped for insufficient lookback/forward window, 9,465 labeled rows (train 6,621 / val 1,418 / test 1,426), class balance checked per split (no class below 20%), and a no-leakage invariant (`train_max_date < val_min_date < val_max_date < test_min_date`) confirmed per ticker directly against the output files. Two new data-quality issues filed (see Known Issues): ENGRO's shorter history, one suspected unadjusted stock-split row.
- [x] **Phase 3 Session 2** — Data-quality fix (3 unadjusted PSX stock splits in `daily_prices` — MARI 10:1 Sep 2024, LUCK 5:1 Apr 2025, UBL 2:1 Jun 2025 — back-adjusted via new `backend/app/ml/split_adjustments.py`, dataset rebuilt, worst forward-5d return now -25% vs previous -88%). XGBoost multi-class classifier trained (`backend/scripts/train_ml_model.py`, seed=42, model saved to `backend/ml_data/model.json`). **Test accuracy 39.34% vs 33.33% random-chance baseline** — a real but very weak edge; FLAT class structurally unlearned (0 predictions). Leakage checks pass (`close`/`date`/`ticker`/`forward_return_5d` confirmed absent from features; per-ticker chronological invariant holds for all 10 tickers). See Build Log 2026-06-27 (Session 2 entry) for full metrics + Session 3 recommendation.
- [x] **Phase 3 Session 3** — Wired the trained model into `Arbitrator.ml_contribution`: `ML_MAGNITUDE=5.0` (5% max swing), `ML_GATE=0.55` (strict `>` on `max(predict_proba)`). Added `build_features_point_in_time()` (sharing `_compute_indicators` with the batch path) plus `backend/app/ml/inference.py` (module-level model singleton, never raises). Orchestrator extends price-pull window to 600 calendar days, calls `predict_from_prices` before agents run, attaches result to `context.ml_signal` (new field on `AgentContext`), repurposes the legacy `IntelligenceReport.ml_beat_probability` column to hold the UP-class probability. `Arbitrator._build_score_breakdown` emits a rich `ml_detail` sub-dict (predicted_class, max_prob, per-class probabilities, gate_passed, skip_reason, model_caveat) so consumers can distinguish "below gate" / "model unavailable" / "insufficient history". LLM prompt to the Arbitrator's narrative call now includes an explicit weak-signal caveat so generated bull/bear cases don't overstate the model's contribution. **Live-verified** against all 10 tickers (probe) + PPL/MCB/UBL (production orchestrator runs) + UBL with `ML_GATE=0.35` in-process (gate-pass code-path demo): all production runs land at `conviction=58.5, ml_contribution=0.0` because no real ticker clears 0.55 (`max_prob` cluster is [0.357, 0.407] across the universe — the gate is silencing a known-weak signal as designed). Score arithmetic hand-checked for all 4 runs. None of `trend_analyzer.py`, `news_synthesizer.py`, `filing_skeptic.py` were modified.
- [ ] **Phase 4** — Next.js 15 frontend (dashboard, company pages, watchlist, alerts UI).
- [ ] **Future / deferred** — Playwright-based PUCARS announcement/PDF scraper; possible CapitalStake integration if productized; possible additional news sources (Dunya News website scraping, since their RSS is blocked but the site itself loads).

## Database — verified live state (Neon Cloud PostgreSQL)

*Last manually verified: June 8, 2026, via direct SQL query — not just pipeline log claims.*

| Table | State |
|---|---|
| `companies` | 10 rows seeded (ENGRO, LUCK, OGDC, PPL, MCB, HBL, UBL, MARI, PSO, MEBL) |
| `daily_prices` | 9,904 rows total across all 10 tickers, ~2 years of history each, 0 collection failures on last full run |
| `news_articles` | 19 rows, all from ARY News, matched against PPL/PSO/OGDC (note: matching is keyword-based and somewhat noisy — see Known Issues) |
| `announcements` | 0 rows — PSX portal scraping not yet functional |
| `intelligence_reports` | 0 rows — Phase 2B not yet built |
| `llm_calls` | 0 rows — no agent has made a real LLM call yet |
| `pipeline_runs` | Multiple clean `SUCCESS` records for `price_collector` and `news_collector` as of the June 8 full run |

**Ticker universe (KSE-30 subset, 10 tickers):** `ENGRO, LUCK, OGDC, PPL, MCB, HBL, UBL, MARI, PSO, MEBL`

## Key files map

| File | Purpose |
|---|---|
| `backend/app/core/config.py` | `get_settings()` singleton, all env vars |
| `backend/app/core/security.py` | JWT creation/verification, password hashing, FastAPI auth dependencies |
| `backend/app/core/redis_client.py` | Async Redis wrapper: caching, rate limiting, circuit breaker state |
| `backend/app/core/llm_gateway.py` | **The most important file.** Single chokepoint for every LLM call. Groq primary, Gemini fallback, circuit breaker, PostgreSQL audit logging via `LLMCall` |
| `backend/app/db/models.py` | All 10 SQLAlchemy models — source of truth for schema |
| `backend/app/db/session.py` | Async engine, `get_db()` FastAPI dependency, `init_db()` |
| `backend/app/agents/base.py` | `AgentContext`, `AgentResult`, `BaseAgent` abstract class with `run()` / `run_safe()` contract |
| `backend/app/agents/orchestrator.py` | `AnalysisOrchestrator` — wires the 4 agents together, builds `AgentContext` from the DB, persists `IntelligenceReport`. Creates the report row before any agent runs so `LLMCall.analysis_id` has a valid FK target |
| `backend/app/collectors/base_collector.py` | `BaseCollector` — same pattern as `BaseAgent`, for data ingestion jobs |
| `backend/app/collectors/pipeline.py` | Orchestrates seed → prices → announcements → PDFs → news, in sequence, per-stage error isolation |
| `backend/app/collectors/price_collector.py` | PSX DPS timeseries fetcher (yfinance removed — see Known Issues) |
| `backend/app/collectors/news_collector.py` | ARY News RSS fetcher with XML-cleaning preprocessing |
| `backend/app/api/v1/companies.py` | `POST /companies/{ticker}/analyze` runs `AnalysisOrchestrator` **inline** and returns the saved `IntelligenceReportResponse` directly (changed in Phase 2B Session 2 — previously queued a Celery task and returned 202) |
| `backend/app/workers/tasks.py` | Celery tasks — `run_analysis` now calls `AnalysisOrchestrator` (Phase 2B Session 2; previously a placeholder that called the data-collection pipeline). Used for nightly/background runs; shares the same orchestrator code path as the API endpoint |
| `backend/scripts/run_pipeline.py` | Standalone CLI runner for manual pipeline testing (`--seed-only`, `--tickers X,Y`) |
| `backend/scripts/diagnose_sources.py` | Diagnostic script that tests each external data source without touching the DB |
| `backend/scripts/test_analysis.py` | Standalone verification script for `AnalysisOrchestrator` — runs real tickers and queries `intelligence_reports`/`llm_calls` directly to confirm persistence |
| `backend/app/ml/features.py` | Phase 3 feature pipeline — `build_features()` (batch, returns a labeled DataFrame for offline training) and `build_features_point_in_time()` (Session 3, returns the latest 11-feature vector as a dict for live inference, or `None` if insufficient trailing history). Both share `_compute_indicators()` so the live vector is identical to what the model was trained on. Pure pandas |
| `backend/app/ml/inference.py` | Phase 3 Session 3 — live inference for the Arbitrator. Lazy-loads `ml_data/model.json` once at module level, then `predict_from_prices(prices, threshold)` returns a rich dict (`available`, `gate_passed`, `skip_reason`, `predicted_class`, `max_prob`, `probabilities`, `as_of_date`). Never raises — degradation is signalled in the result dict. Imported by `orchestrator.py`; not imported by `features.py` so the offline path stays import-free |
| `backend/scripts/probe_ml_signal.py` | Read-only diagnostic — runs `predict_from_prices` against every ticker in the DB and prints class/prob/gate-status. No LLM calls, no writes. Use to check "is the gate doing anything?" before/after any model retrain |
| `backend/scripts/verify_ml_wiring.py` | Phase 3 Session 3 verification script — 4 parts (probe / production orchestrator run on PPL+MCB+UBL / gate-pass code-path demo with ML_GATE temporarily lowered to 0.35 / direct SQL dump of `score_breakdown.ml_detail`). DOES write IntelligenceReport rows. Restores production constants on exit |
| `backend/app/ml/split_adjustments.py` | Phase 3 Session 2 — `SPLIT_ADJUSTMENTS` table + `apply_split_adjustments()` helper. PSX DPS doesn't adjust raw closes for stock splits — this back-adjusts pre-split close/open/high/low/volume by an empirical ratio so the price series is continuous across split days. Currently covers MARI 2024-09-16, LUCK 2025-04-28, UBL 2025-06-23. Add new rows here if new splits show up |
| `backend/scripts/build_ml_dataset.py` | Phase 3 dataset builder — pulls all 10 tickers from the live DB, applies `apply_split_adjustments()`, runs `features.py` per ticker, does a per-ticker chronological 70/15/15 split, writes `backend/ml_data/{train,val,test}.parquet` (gitignored) |
| `backend/scripts/train_ml_model.py` | Phase 3 Session 2 — XGBoost multi-class trainer + evaluator. Loads the parquet files, fits with early stopping on val, reports test accuracy / per-class P-R-F1 / confusion matrix / feature importances, saves `ml_data/model.json` and `ml_data/metrics.json`. Re-runnable end-to-end, seed=42 |
| `backend/scripts/find_split_row.py` | Read-only diagnostic — prints top extreme single-day and 5-day-forward returns across all tickers. Use to discover any new unadjusted corporate actions before adding them to `split_adjustments.py` |
| `backend/scripts/verify_dataset.py` | Read-only sanity check on the parquet files — most-extreme returns per split + per-ticker chronological-invariant check |

## Environment / secrets (never commit these — they live in `.env`, gitignored)

`DATABASE_URL` (Neon Cloud, asyncpg), `SECRET_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, 
`REDIS_URL` (Upstash, `rediss://`), `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` 
(also pointed at Upstash, db indexes 1/2), `PSX_TICKERS`, `NIGHTLY_PIPELINE_HOUR`, 
`FRONTEND_URL`. Template lives in `backend/.env.example` (tracked in git).

## Known environment quirks (full detail in `docs/KNOWN_ISSUES.md`)

- `bcrypt` must stay pinned to `4.0.1` — `passlib==1.7.4` breaks with bcrypt 4.1+.
- Neon Cloud's SSL connection string (`sslmode=require&channel_binding=require`) is not 
  directly compatible with asyncpg's URL parser — handled via custom logic in `session.py`.
- Windows terminal (cp1252) can't render the `✓` unicode character in logs — replaced with `[OK]`.

## Working conventions for this project

- Every phase ends with a **live verification step against the actual database**, not 
  just trusting a completion summary. This is a hard-learned lesson from Phase 2A (see 
  Build Log, May 30) where a session claimed success but had silently failed on 3 of 4 
  data sources.
- Antigravity sessions were split into smaller chunks (e.g. "Phase 1A" vs "Phase 1B") 
  specifically to avoid the AI cutting corners on later files in a long single-shot prompt.
- When a phase prompt is run, it should always end by asking for a structured completion 
  summary (files generated, what was verified, what's still a placeholder, what the next 
  phase needs) — this pattern has worked well and should continue.
- This project is being actively shifted from Antigravity (Gemini 3.1 Pro / Claude via 
  Antigravity) to Claude Code directly, partly because Antigravity sessions lost context 
  and hit model quota limits mid-execution more than once.