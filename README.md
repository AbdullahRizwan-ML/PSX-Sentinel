# PSX Sentinel

**AI Financial Intelligence Platform for the Pakistan Stock Exchange** — a machine-learning price-direction model and a 4-agent autonomous research pipeline, fused into a single explainable conviction score per stock.

> Most AI finance demos hallucinate confidence. PSX Sentinel is engineered to do the opposite: every score is decomposable into auditable terms, every LLM call is logged with its cost, and when the data isn't good enough to say something — it says nothing.

---

## What it does

For each company in its KSE-30 universe, PSX Sentinel runs a nightly research cycle:

1. **Collects** prices, news, corporate filings, fundamentals, and institutional money flows from six live Pakistani data sources.
2. **Analyzes** them with four specialist AI agents — a technical trend analyst, a news synthesizer, a skeptical filings auditor, and an arbitrator.
3. **Predicts** 5-day price direction with a trained XGBoost model, gated so a weak signal can't move the score.
4. **Scores** everything into a single 0–100 conviction number with a fully transparent breakdown:

```
conviction = 50 + technical(±20) + news(±15) + filings(−5/−15/−30)
                + ml(±5, confidence-gated) + fundamentals(±10) + flows(±10)
```

Every term ships with an audit trail. When the filings auditor knocked PSO from 50 → 35, it was because it read the actual PDF of an unexplained board resignation — and the red flag it raised was spot-checked against the source document.

## The four agents

| Agent | Role |
|---|---|
| **TrendAnalyzer** | Reads price action — moving averages, RSI, momentum, volatility, 52-week range position |
| **NewsSynthesizer** | Separates genuinely relevant news from keyword-match noise; skips the LLM entirely when there's nothing real to read |
| **FilingSceptic** | A skeptical auditor over corporate announcements — downloads and reads the actual filing PDFs (full extracted text where the PDF has a text layer, honest title-only fallback where it's an image scan), and flags red flags with severity |
| **Arbitrator** | Combines all signals deterministically into the conviction score, then writes the bull/bear narrative — with explicit instructions not to overstate weak signals |

No CrewAI. No LangChain. The orchestration is custom Python — deterministic, debuggable, and observable, because black-box agent frameworks are exactly what you don't want under a number people might trust.

## The ML signal — honestly reported

A 3-class XGBoost classifier (UP / DOWN / FLAT, 5-trading-day horizon) trained on ~5 years of split-adjusted daily prices across the universe:

- **Test accuracy: 43.2%** vs a 33.3% random baseline — and it beats the always-predict-UP naive baseline (40.8%), which the first model version didn't. That improvement is documented, not buried.
- **Backtested out-of-sample** with `vectorbt`, net of realistic PSX costs (0.30% round-trip commission + 15% CGT): the ungated strategy returned **+18.4% vs +13.8% buy-and-hold** (Sharpe 1.12 vs 0.81) over the test window; the confidence-gated variant cut max drawdown from −21.8% to **−4.3%**.
- **The honest read, straight from the docs:** this is a real but thin edge validated on a single bullish regime — not a deployable trading strategy. The production system gates the signal at 55% confidence, and since no live prediction currently clears that bar, the ML term contributes exactly **0.0** to every production score. The gate silencing a weak signal is the system working as designed.
- Leakage is proven, not assumed: per-ticker chronological splits with machine-checked boundary invariants, re-verified on every retrain.

## Data engineering in a hostile environment

Pakistani market data is not sitting in a tidy API. Getting it required real source archaeology:

- **PSX official price feed** — reverse-engineered; discovered it serves a fixed rolling ~5-year window and silently ignores all date parameters, so the database is the archive, not a cache. 13,000+ daily bars, with unadjusted stock splits (MARI 10:1, LUCK 5:1, UBL 2:1) detected via outlier forensics and back-adjusted.
- **NCCPL institutional flows (FIPI/LIPI)** — mapped an undocumented internal JSON API behind a Cloudflare challenge; **268,000+ rows** of foreign and local institutional buying/selling across 1,267 trading days feed a sector money-flow regime term in the score.
- **PSX Terminal fundamentals** — the documented REST API is dead server-side, so the integration parses the site's internal SvelteKit SSR payloads instead.
- **News** — ARY and Dunya News scrapers with per-article relevance judgment, because raw ticker keyword matching is mostly noise.
- **Filing PDFs** — downloaded and text-extracted; 61% have real text layers, 39% are image-only scans that the system explicitly refuses to invent text for. No OCR guessing, no hallucinated filings.

Dead sources (Yahoo Finance, Dawn/Business Recorder RSS, static PSX portal scraping) are documented as dead in `docs/KNOWN_ISSUES.md` rather than left as silent failures.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Next.js 15 frontend — "Karachi Dusk" design system         │
│  ConvictionDial gauge · price charts · dark mode · watchlist│
└──────────────────────────┬──────────────────────────────────┘
                           │ typed API client (single chokepoint)
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI (fully async) · JWT auth · Redis caching           │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  AnalysisOrchestrator                                  │ │
│  │  TrendAnalyzer · NewsSynthesizer · FilingSceptic       │ │
│  │           └──────────► Arbitrator ◄──── XGBoost signal │ │
│  └───────────────────────────┬────────────────────────────┘ │
│                              │                              │
│  ┌───────────────────────────▼────────────────────────────┐ │
│  │  LLMGateway — the single chokepoint for every LLM call │ │
│  │  Groq (Llama 3.3-70B) → Gemini 2.0 Flash failover      │ │
│  │  circuit breaker · cost tracking · full audit log      │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│  PostgreSQL (Neon) · Redis (Upstash) · Celery nightly jobs  │
│  6 data collectors with per-item failure isolation          │
└─────────────────────────────────────────────────────────────┘
```

**Design principles that are actually enforced:**

- **One LLM chokepoint.** No agent or collector ever touches an LLM SDK directly — everything routes through `LLMGateway`, which is what makes cost tracking, circuit breaking, and audit logging free everywhere. Verified via the audit table, not just code review.
- **Graceful per-item failure.** One ticker failing never takes down a pipeline run.
- **No data → no LLM call.** Agents return low-confidence results instead of hallucinating analysis from nothing.
- **Honest zeros are distinguishable from missing data.** A computed 0.0 contribution, a stale-data skip, and a pre-feature NULL are three different things in the schema — and the frontend renders them differently.
- **Everything is verified against the live database.** Every build phase ends with direct SQL verification, not a log line that claims success.

## Frontend

A Next.js 15 (App Router) + TypeScript app with a hand-built design system — **"Karachi Dusk"** (deep teal, terracotta, warm cream; Fraunces + Inter) — deliberately not a Streamlit dashboard or stock component library. Highlights:

- **ConvictionDial** — a custom SVG gauge that makes a 0–100 score readable in under 100ms
- TradingView-powered price charts (close + MA20/MA50 + volume) with an honest caption explaining why there are no candlestick wicks (the source has no real intraday range — fake wicks would be decoration)
- Full ML-signal transparency card: per-class probabilities, gate status, and *why* a signal was or wasn't used
- Dark mode, mobile nav, optimistic-update watchlist, designed empty/loading/error states throughout

<p align="center">
  <img src="assets/Swagger1.png" alt="API endpoints — Swagger UI" width="800">
</p>

## Tech stack

| Layer | Choices |
|---|---|
| Backend | FastAPI · SQLAlchemy 2.0 (async) · Pydantic v2 · Alembic |
| Data & queue | PostgreSQL (Neon) · Redis (Upstash) · Celery |
| AI | Custom agent orchestration · Groq (Llama 3.3-70B) + Gemini 2.0 Flash failover · XGBoost |
| Collection | httpx · pdfplumber · Playwright · custom SSR-payload parsers |
| Backtesting | vectorbt · pandas |
| Frontend | Next.js 15 · TypeScript · Tailwind · lightweight-charts |
| Auth | JWT (access + refresh) · bcrypt |

## Quickstart

**Prerequisites:** Python 3.11+, Node 18+, a PostgreSQL instance, a Redis instance.

```bash
# ── Backend ──────────────────────────────────────────────
cd backend
python -m venv venv
venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env           # then fill in your credentials
uvicorn app.main:app --reload --port 8000
# Swagger UI → http://localhost:8000/docs

# ── Frontend ─────────────────────────────────────────────
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

Required environment variables (see `backend/.env.example`): `DATABASE_URL` (asyncpg), `REDIS_URL`, `SECRET_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`.

Run the data pipeline manually:

```bash
python backend/scripts/run_pipeline.py            # full pipeline
python backend/scripts/run_pipeline.py --prices-only
python backend/scripts/run_pipeline.py --news-only --tickers PPL,MCB
```

## Project documentation

The repo's documentation practices are part of the point:

- **`docs/BUILD_LOG.md`** — a session-by-session engineering log of every build phase, including what broke, what was reversed, and why
- **`docs/KNOWN_ISSUES.md`** — every dead data source, data-quality caveat, and unresolved problem, kept current so no one re-attempts a dead end
- **`docs/BACKTEST_RESULTS.md`** — full backtest methodology and numbers, including the caveats that make the headline figure smaller

---

### Disclaimer

*PSX Sentinel is a technical portfolio project demonstrating software architecture, LLM orchestration, ML engineering, and data collection under real-world constraints. It is **not** a registered financial advisor. Nothing it generates constitutes financial, investment, or trading advice — the backtest documentation itself explains why you shouldn't trade on it.*
