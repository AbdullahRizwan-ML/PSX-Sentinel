# PSX Sentinel — Project Context for Claude

> **New session? Read in this order:** this file, then `docs/KNOWN_ISSUES.md`, 
> then the last 3-4 entries of `docs/BUILD_LOG.md`. Do not assume anything 
> works until you've checked the live database yourself — a "complete" status 
> from a previous session or chat history is not verification. Run the actual 
> query. This project has already been burned once by trusting an AI's claim 
> of success without checking the database directly (Phase 2A, May 30).

## What this is

Enterprise-grade AI financial intelligence platform for the Pakistan Stock 
Exchange (PSX). Combines an ML earnings prediction model with a 4-agent 
autonomous research pipeline. This is a portfolio and career-leverage project 
for a final-year Data Science student — it must read as production quality 
to recruiters and technical interviewers, not as a student project.

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
- [ ] **Phase 2B** — IN PROGRESS / NEXT: Four specialist agents (`TrendAnalyzer`, `NewsSynthesizer`, `FilingSceptic`, `Arbitrator`) + `AnalysisOrchestrator` that wires them together, builds `AgentContext` from the live database, and produces a saved `IntelligenceReport`. Wire into the existing `POST /companies/{ticker}/analyze` endpoint and the Celery `run_analysis` task (currently a placeholder).
- [ ] **Phase 3** — ML pipeline: XGBoost + LightGBM earnings/price-movement prediction, adapted from the existing EarningsPulse architecture, retrained on PSX data. Feeds a probability score into the Arbitrator's conviction score calculation (currently a 0%-weighted placeholder in the scoring formula).
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
| `backend/app/collectors/base_collector.py` | `BaseCollector` — same pattern as `BaseAgent`, for data ingestion jobs |
| `backend/app/collectors/pipeline.py` | Orchestrates seed → prices → announcements → PDFs → news, in sequence, per-stage error isolation |
| `backend/app/collectors/price_collector.py` | PSX DPS timeseries fetcher (yfinance removed — see Known Issues) |
| `backend/app/collectors/news_collector.py` | ARY News RSS fetcher with XML-cleaning preprocessing |
| `backend/app/workers/tasks.py` | Celery tasks — `run_analysis` is currently a placeholder pending Phase 2B |
| `backend/scripts/run_pipeline.py` | Standalone CLI runner for manual pipeline testing (`--seed-only`, `--tickers X,Y`) |
| `backend/scripts/diagnose_sources.py` | Diagnostic script that tests each external data source without touching the DB |

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