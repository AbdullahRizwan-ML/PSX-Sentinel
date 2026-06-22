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

---

<!-- Next entry goes here. Add a new ## dated heading below this line. -->