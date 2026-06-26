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

<!-- Next entry goes here. Add a new ## dated heading below this line. -->