# Backtest Results

> Home for out-of-sample trading backtests of PSX Sentinel's models. Each
> run records the model artifact, the exact split window, the cost
> assumption, and the literal result numbers vs a buy-and-hold benchmark.
> Reproduce with `python backend/scripts/backtest_xgboost.py` (read-only —
> no DB writes, no retrain). When a model is retrained, append a new dated
> section rather than overwriting; keep the old numbers so regressions are
> visible.

---

## Phase 6 Session 2 — composite conviction score vs forward returns, first-ever check (2026-09-10)

**One-line answer:** across 200 point-in-time (ticker, date) pairs, the
deployed composite conviction score showed **no detectable relationship
with forward 5-day returns — Pearson r = −0.025, p = 0.72, 95% CI
[−0.163, +0.114]**. The tercile means run mildly *backwards* (low-score
pairs +0.751%, high-score +0.521%), but the 0.23pp spread is far inside
the noise of a 4.83% forward-return standard deviation. **The honest read
is "this shows nothing", not "this shows the score is inverted."**

**What is different about this run:** every previous backtest in this doc
tested the *raw XGBoost model alone*, replayed from a static parquet. This
is the first time the **composite score a user actually sees** — technical
+ news + filing + ML + flow, as computed by the live `Arbitrator` — has
been checked against what the stock did next. Reproduce with
`python backend/scripts/backtest_conviction_scores.py`.

### ⚠️ Scope limitation, stated up front: this is a FIVE-term score, not the six-term one production serves

`peer_fundamentals` is deliberately passed empty, so
`fundamentals_contribution` is an honest **0.0 on all 200 pairs**.
`company_fundamentals` is a current-snapshot table with no history
(docs/KNOWN_ISSUES.md, Phase 5 Session 5) — joining today's P/E onto a
2025-12 row would be lookahead bias. **So this result does not test the
score the live system actually serves; it tests that score minus its
fundamentals term.** Building point-in-time fundamentals is separate,
larger work (PSX Terminal's `fyReports` carries per-FY
`earnings_release_date`), not done here.

### Setup

| | |
|---|---|
| Sample | 10 active tickers × 20 dates = **200 pairs** |
| Dates | 20 evenly spaced calendar dates, 2025-11-27 → 2026-07-10 |
| Window source | shared ML test window, re-read live from `ml_data/test.parquet` |
| Context | `PointInTimeContextBuilder` (Phase 6 Session 1), every row bounded `date <= T` |
| Agents | `TrendAnalyzer`, `NewsSynthesizer`, `FilingSceptic` — real LLM calls, no mocking |
| Score | `Arbitrator`'s own scoring methods, called verbatim |
| Outcome | realized forward 5-trading-day return, `HORIZON_DAYS` + formula reused from `app/ml/features.py`, split-adjusted |
| Leakage | **0 failures / 200** — every pair's prices, news, announcements and flows re-verified in Python against the rows actually returned |
| DB writes | **no `intelligence_reports` rows** (hard rule — these are synthetic scores on pretend-historical "today"s). `llm_calls` audit rows only, `analysis_id` NULL |

**The Arbitrator's narrative LLM call was skipped**, and that is sound:
`arbitrator.py` computes the score deterministically
(`_fundamentals_contribution`, `_flow_contribution`, `_calculate_score`,
`_score_to_label`, `_build_score_breakdown`) *before* `_build_prompt` /
`self.llm.complete()`, and the LLM response feeds only bull/bear prose.
The score arithmetic is used verbatim, not reimplemented. Proven, not
asserted: `llm_calls` was queried for `agent_name='arbitrator'` over the
run window and returned **0 rows**.

### Result — correlation and terciles

| subset | n | Pearson r | p | 95% CI | low tercile | mid | high |
|---|---|---|---|---|---|---|---|
| All pairs | 200 | **−0.0252** | 0.723 | [−0.163, +0.114] | +0.751% | +0.618% | +0.521% |
| Clean only (no agent failure) | 174 | **−0.0214** | 0.779 | [−0.170, +0.128] | +0.792% | +0.601% | +0.491% |

Conviction scores ranged 19.0 → 68.5 (mean 51.03, sd 9.67, 95 distinct
values). Forward returns: mean **+0.629%**, sd 4.83%, 54.5% positive —
this window was broadly bullish, consistent with the Session 6 finding
that the test tail is a strongly bullish period.

Per-ticker correlations (n=20 each) spread −0.350 (MCB) to +0.281 (MEBL),
mean −0.021. At n=20 a single r needs roughly |r| > 0.44 to clear 95%
significance, so **every one of those is consistent with zero** — the
spread is what pure noise across 10 tickers looks like, and no individual
ticker is being claimed as a finding here.

### Which terms actually moved the score

This is the real story behind a null correlation — reported from the data,
not asserted:

| term | % of pairs non-zero | min | max | sd |
|---|---|---|---|---|
| `flow_contribution` | **90.0%** | −10.00 | +10.00 | 4.87 |
| `technical_contribution` | **72.0%** | −17.00 | +17.00 | 6.94 |
| `ml_contribution` | 7.5% | 0.00 | +5.00 | 1.32 |
| `filing_contribution` | 4.5% | −15.00 | 0.00 | 2.43 |
| `news_contribution` | **0.0%** | 0.00 | 0.00 | 0.00 |
| `fundamentals_contribution` | 0.0% (excluded by design) | 0.00 | 0.00 | 0.00 |

So the composite under test is, in practice, **technical + sector flow**,
with occasional ML and filing input. Two things follow:

- **`news_contribution` fired on literally zero of 200 pairs.** Not a bug
  — `news_articles` is a rolling recent-only mirror (only PPL/PSO/OGDC
  ever matched, only from June 2026), so for nearly the whole historical
  window there is no news to analyse, and where there was,
  `NewsSynthesizer` judged nothing relevant. The news term is structurally
  dead in any backtest over this window.
- **The ML gate passed on 15/200 pairs (7.5%), with `max_prob` reaching
  0.764.** This is genuinely new: `docs/KNOWN_ISSUES.md` records that in
  production the gate *never* clears (latest-day cluster 0.348–0.467), and
  this run reproduces that low end (min 0.348) — but historically the gate
  *was* reachable. The gate is rarely-firing, not structurally unreachable.

### Data-quality caveat — Groq's daily token cap degraded 26 pairs

Partway through, the run exhausted **Groq's free-tier 200,000 tokens-per-day
limit** (`Limit 200000, Used 199733`). After that: 26 Groq calls returned
429, **17 were rescued by the Gemini fallback** (`gemini-3.6-flash` — fixed
in the 2026-09-09 hotfix, earning its keep on its first real outage), 8
failed on both providers, and the circuit breaker — live again after the
2026-09-09 Redis restore — rejected 46 further calls.

Effect: **26 of 200 pairs** had at least one agent fail, so those agents
contributed 0.0 and biased those scores toward 50. The damage is confined
to **UBL (all 20 pairs) and PSO (6)** — the alphabetically-last tickers,
reached when the budget ran out. Because a failed agent's contribution is
an honest zero rather than a wrong number, and because the clean-subset
correlation (−0.0214) is materially identical to the full-sample one
(−0.0252), **the conclusion is robust to this degradation** — but UBL is
effectively absent from the clean subset and that should be remembered
before treating n=174 as a clean 10-ticker sample. It is 9 tickers plus a
partial PSO.

**Operational consequence for future sizing:** at ~1,000 tokens/pair, the
Groq free tier caps a run at roughly **200 pairs per day**. A larger
backtest must span multiple days, deliberately route to Gemini, or move off
the free tier. Session 1's cost estimate (~347 calls / ~332K tokens for 200
pairs) was *over* on tokens — actual was **233 calls / 203,354 tokens /
~30 min** — but it never modelled a daily ceiling, which turned out to be
the real binding constraint.

### Honest reading of the result

- **The question this phase opened with now has a first answer, and the
  answer is "no measurable edge."** r = −0.025 at n=200 is
  indistinguishable from zero. Nothing here says the score is inverted;
  nothing says it works.
- **This is a sanity check, not a powerful test.** 200 points, one
  ~7.5-month window, one broadly bullish regime, 5-day horizon only, no
  transaction costs, no trading rule — this measures association, not
  profitability. The Sharpe/drawdown machinery used in the Session 1/6
  model backtests was deliberately not applied to a score with no
  demonstrated signal.
- **A null result was a realistic outcome given what the terms are.** The
  two live terms are a technical read (whose underlying ML cousin scores
  ~43% on 3-class direction) and a *sector-level* flow regime whose own
  Session 8 exploration measured only ~+0.05 correlation with forward
  sector returns. A composite of two weak, partly-sector-level signals
  showing ~0 correlation at n=200 is consistent with what was already
  documented — it is not a new failure.
- **What this does not rule out:** the missing fundamentals term, a
  different horizon, a non-linear or regime-conditional relationship, or
  score *changes* rather than score *levels*. Those are open, and this run
  is not evidence against them.

---

## Phase 5 Session 6 — retrained model (full-depth window, ENGROH universe), backtest re-run (2026-07-18)

**One-line answer:** the retrained model (same 11 features, same
hyperparameters, trained on the full 2021-06 → 2026-07 depth with ENGROH
replacing delisted ENGRO) beat buy-and-hold by more than the old model did —
ungated **+18.36%** vs B&H **+13.81%** pre-CGT (Sharpe 1.12 vs 0.81), and the
gated rule hit Sharpe **0.99** with only a **−4.30%** max drawdown — but the
new test window (2025-11-27 → 2026-07-10) is a *later, more bullish, largely
overlapping* period vs Session 1's, not a wider multi-regime one, so this is
a modestly stronger claim, not a categorically stronger one.

### Before/after — Session 1 (old model, old split) vs Session 6 (retrained, new split)

All figures: equal-weight sleeve, 0.15%/side (0.30% round-trip) commission,
Sharpe annualised ×√252. **The two test windows differ** (old: 9 tickers,
2025-10-24 → 2026-05-29; new: 10 tickers incl. ENGROH, 2025-11-27 →
2026-07-10, ~6 months overlap), so compare each strategy *against its own
window's buy-and-hold*, not raw return to raw return.

| Metric | S1 ungated | S6 ungated | S1 gated | S6 gated | S1 B&H | S6 B&H |
|---|---:|---:|---:|---:|---:|---:|
| Total return (pre-CGT) | +5.10% | **+18.36%** | +2.35% | **+5.57%** | +3.33% | +13.81% |
| Total return (post-CGT) | +4.33% | +15.60% | +2.00% | +4.74% | +2.83% | +11.74% |
| Ann. Sharpe | +0.43 | **+1.12** | +0.42 | **+0.99** | +0.33 | +0.81 |
| Max drawdown | −21.30% | −19.37% | −5.70% | **−4.30%** | −24.41% | −21.79% |
| Win rate | 63.49% | 61.43% | 63.64% | 69.23% | n/a | n/a |
| Trades | 63 | 70 | 22 | 26 | 0 | 0 |
| Excess return vs own-window B&H (pre-CGT) | +1.77pp | **+4.55pp** | −0.98pp | −8.24pp | — | — |
| Excess Sharpe vs own-window B&H | +0.10 | **+0.31** | +0.09 | +0.18 | — | — |

**Model accuracy:** 43.19% (new) vs 39.34% (old), against a 33.33%
random-chance baseline. More telling: the **old model was *below* its test
set's always-UP naive baseline (39.34% vs 40.25%); the new one is *above*
its own (43.19% vs 40.81%, +2.4pp)** — the retrain is the first time the
model beats the majority-class strategy, not just random chance.

### Setup (what changed vs Session 1, what didn't)

| Item | Value |
|---|---|
| Model | `backend/ml_data/model.json` — retrained 2026-07-18 (Phase 5 Session 6), same 11 features, same target, **identical hyperparameters** (seed=42, early stopping on val; best iteration 34 vs old 27). Old artifact archived as `model_phase3s2_backup.json` |
| Training data | Rebuilt 70/15/15 per-ticker chronological split on the full-depth window: 10,050 labeled rows (train 7,034 / val 1,504 / test 1,512) vs old 9,465 (6,621/1,418/1,426). Universe: **ENGROH in, delisted ENGRO out** — all 10 tickers now share one common calendar (no more disjoint window) |
| Test window | 2025-11-27 → 2026-07-10 (~7.4 months; ENGROH starts 2025-12-08 — 11 days later, handled as flat cash before entry). **Similar length to Session 1's window, shifted ~1 month later, ~6 months overlapping — NOT a wider multi-regime window** (see honest reading) |
| Rows clearing the 0.55 gate | 99 of 1,512 (6.5%); all UP (vs 105/1,426 = 7.4% old) |
| Everything else | Identical to Session 1: long-only regime-hold, close-to-close, equal-weight, one position/ticker, no leverage, `vectorbt==1.0.0`, 0.15%/side commission (0.30% round-trip), 15% CGT post-hoc applied identically to B&H, slippage not modelled (same optimistic caveat), 0.05%/side discount sensitivity below. Cost rationale unchanged — see the Session 1 section |

### Headline sleeve — 10 equal-weight tickers, shared window 2025-11-27 → 2026-07-10

(Sleeve capital = 10 × $10,000 = $100,000.)

| Strategy | Total return (pre-CGT) | Total return (post-CGT) | Ann. Sharpe | Max drawdown | Win rate | Trades |
|---|---:|---:|---:|---:|---:|---:|
| **Ungated** (long every UP) | **+18.36%** | +15.60% | **+1.12** | −19.37% | 61.43% | 70 |
| **Gated** (max_prob > 0.55) | +5.57% | +4.74% | +0.99 | **−4.30%** | 69.23% | 26 |
| **Buy & Hold** (benchmark) | +13.81% | +11.74% | +0.81 | −21.79% | n/a | 0 |

**Cost sensitivity (discount broker, 0.05%/side instead of 0.15%/side):**

| Strategy | Total return (pre-CGT) | Post-CGT | Ann. Sharpe | Max DD | Win rate | Trades |
|---|---:|---:|---:|---:|---:|---:|
| Ungated @ 0.05%/side | +20.09% | +17.08% | +1.20 | −19.17% | 67.14% | 70 |
| Buy & Hold @ 0.05%/side | +13.83% | +11.76% | +0.82 | −21.79% | n/a | 0 |

### Per-ticker (all on the shared window; ENGROH enters 2025-12-08)

| Ticker | #UP | #gate | Ungated ret | Ungated trades | Gated ret | Gated trades | Buy & Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| ENGROH | 140 | 2 | +39.17% | 5 | +10.43% | 2 | +29.87% |
| HBL | 148 | 12 | −9.01% | 4 | +3.69% | 3 | +2.99% |
| LUCK | 131 | 17 | −2.56% | 10 | +0.53% | 2 | +3.64% |
| MARI | 33 | 7 | +23.56% | 20 | −0.62% | 1 | −3.61% |
| MCB | 151 | 4 | +18.44% | 2 | +14.52% | 3 | +17.85% |
| MEBL | 149 | 3 | +14.25% | 4 | +12.59% | 2 | +29.81% |
| OGDC | 150 | 4 | +32.63% | 3 | +10.99% | 1 | +31.67% |
| PPL | 136 | 12 | +22.50% | 7 | +0.09% | 3 | +19.04% |
| PSO | 93 | 17 | −14.82% | 8 | −0.66% | 4 | −21.49% |
| UBL | 110 | 21 | +59.77% | 14 | +4.17% | 5 | +28.65% |

### No-leakage proof (test-split only)

Same double proof as Session 1: the backtest asserts `test_min > val_max`
per ticker on the parquet it trades, and `verify_dataset.py` independently
re-derives the split boundaries — exact match for all 10 tickers:

| Ticker | val_max | test_min | test_max | test rows |
|---|---|---|---|---:|
| ENGROH | 2025-12-05 | 2025-12-08 | 2026-07-10 | 145 |
| HBL | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| LUCK | 2025-11-27 | 2025-11-28 | 2026-07-10 | 151 |
| MARI | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| MCB | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| MEBL | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| OGDC | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| PPL | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| PSO | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |
| UBL | 2025-11-26 | 2025-11-27 | 2026-07-10 | 152 |

`test_min > val_max` for all 10 → no train or val row is ever traded. The
5-day forward label remains a training target only, never a trading input.

### Honest reading of the result

Better, still thin, and **not** the multi-regime upgrade the session brief
hoped for:

1. **The premise "the old split was built on a much shorter window" was
   wrong.** The Phase 3 Session 1 dataset already read ~5 years/ticker —
   PSX DPS always served its full rolling window regardless of the old
   `HISTORY_DAYS=730` setting. The real deltas this retrain adds are
   ENGROH's full history replacing delisted ENGRO's truncated one, plus
   ~6 weeks of newer data: 10,050 labeled rows vs 9,465 (+6.2%).
2. **The new test window is not "much wider, multi-regime".** More data
   shifts the 15% chronological tail *later*, it doesn't widen it: ~7.4
   months (2025-11-27 → 2026-07-10) vs ~7.2 months, overlapping the old
   window by ~6 months. This remains ONE window, ONE regime — a strongly
   bullish one (B&H +13.81% in 7.4 months). The Session 1 caveat stands
   unchanged: this validates the signal is not worthless after costs; it
   does not establish a deployable strategy.
3. **The genuine improvements:** the model now beats the always-UP naive
   baseline (+2.4pp), which the old model did not; ungated excess return
   over its own window's B&H grew from +1.77pp to +4.55pp and excess
   Sharpe from +0.10 to +0.31; and the gated rule now posts Sharpe 0.99
   with a −4.30% max drawdown — its risk-reduction character is intact
   and slightly better.
4. **New caveat — the retrained model is heavily UP-skewed:** 82% of its
   test predictions are UP (DOWN recall 0.20, FLAT still structurally
   unlearned at 3 predictions). In a bullish window that skew flatters
   both accuracy and the ungated return; in a sustained bear market this
   model would be long nearly the whole way down. The ungated rule's
   value-add over B&H comes from a handful of well-timed exits, not from
   calling downturns.
5. **Production behavior unchanged:** live probe (2026-07-18) shows the
   max_prob cluster at 0.368–0.434 across all 10 tickers — still nobody
   clears the 0.55 gate on the latest day, so `ml_contribution` remains
   0.0 in production, by design. Historically 99/1,512 test rows (6.5%)
   cleared it.

---

## Phase 5 Session 1 — XGBoost price-direction model, first-ever backtest (2026-07-04)

**One-line answer:** on the held-out test window, trading every UP
prediction (ungated) returned **+5.10%** vs buy-and-hold **+3.33%** (net of
0.30% round-trip commission, before CGT), with a marginally better Sharpe
(0.43 vs 0.33) — a real but thin edge, entirely consistent with the model's
known ~+6pp accuracy edge. The production-gated version (max_prob > 0.55)
traded far less and returned **+2.35%**, below buy-and-hold on return but at
a fraction of the risk (max drawdown −5.70% vs −24.41%).

### Setup

| Item | Value |
|---|---|
| Model | `backend/ml_data/model.json` — the Phase 3 Session 2 XGBoost model, **loaded as-is, not retrained** |
| Data | `backend/ml_data/test.parquet` — the **test split only** (1,426 rows, 10 tickers) |
| Rows clearing the 0.55 gate | 105 of 1,426 (7.4%); all 105 are UP predictions |
| Execution | Close-to-close only (no intraday — `high`/`low` are derived approximations) |
| Direction | Long-only (retail PSX short-selling is impractical); DOWN/FLAT → flat/cash |
| Sizing | Equal-weight, one position per ticker at a time, no leverage, no pyramiding (`accumulate=False`) |
| Sharpe basis | Daily returns × √252 |
| Engine | `vectorbt==1.0.0` (`Portfolio.from_signals` / `from_holding`) |

### Transaction-cost assumption (stated explicitly)

- **Commission + statutory per-trade charges: 0.15% per side ⇒ 0.30%
  round-trip**, applied by vectorbt as `fees` on both the entry and exit
  close. Rationale: PSX retail brokerage in practice ranges from ~0.03–0.05%
  per side (online/discount brokers) up to the older ~0.15% / 2.5-paisa-per-
  share conventional rate; on top sit FED / provincial sales tax on the
  commission (~13–16% *of* the commission), CDC charges, and SECP/PSX/NCCPL
  levies (each a fraction of a bp). 0.15%/side is adopted as a single
  conservative bundle deliberately on the expensive side — if the edge
  survives 0.30% round-trip it is likelier to survive real life. A
  **0.05%/side discount-broker sensitivity** is reported alongside.
- **Capital Gains Tax: 15% of net realised gain**, applied once, post-hoc,
  at the sleeve level (PSX CGT for a filer holding < 12 months is a flat 15%;
  all holds here are days long). CGT is *not* folded into per-trade `fees`
  because it only bites net winners and is offset by losses — taxing losing
  trades would be wrong. Both pre-CGT and post-CGT figures are reported, and
  the **same 15%-on-net-gain haircut is applied to buy-and-hold** for an
  apples-to-apples comparison.
- **Slippage: not separately modelled** (close-to-close fills assumed
  achievable at the printed close). On thin PSX names this is optimistic — a
  known simplification, flagged here and in the script.

### Headline sleeve — 9 equal-weight tickers, shared window 2025-10-24 → 2026-05-29

(ENGRO excluded from the sleeve — its test window is a disjoint 2024 period;
see below. Sleeve capital = 9 × $10,000 = $90,000.)

| Strategy | Total return (pre-CGT) | Total return (post-CGT) | Ann. Sharpe | Max drawdown | Win rate | Trades |
|---|---:|---:|---:|---:|---:|---:|
| **Ungated** (long every UP) | **+5.10%** | +4.33% | **+0.43** | −21.30% | 63.49% | 63 |
| **Gated** (max_prob > 0.55) | +2.35% | +2.00% | +0.42 | **−5.70%** | 63.64% | 22 |
| **Buy & Hold** (benchmark) | +3.33% | +2.83% | +0.33 | −24.41% | n/a | 0¹ |

¹ Buy-and-hold holds 9 open positions to the end of the window; it books
**0 closed round-trips**, so per-trade win rate is undefined (n/a).

**Cost sensitivity (discount broker, 0.05%/side instead of 0.15%/side):**

| Strategy | Total return (pre-CGT) | Post-CGT | Ann. Sharpe | Max DD | Win rate | Trades |
|---|---:|---:|---:|---:|---:|---:|
| Ungated @ 0.05%/side | +6.52% | +5.54% | +0.50 | −20.94% | 68.25% | 63 |
| Buy & Hold @ 0.05%/side | +3.34% | +2.84% | +0.33 | −24.41% | n/a | 0 |

### Per-ticker (each on its own test window; `*` = disjoint 2024 window)

| Ticker | #UP | #gate | Ungated ret | Ungated trades | Gated ret | Gated trades | Buy & Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| ENGRO * | 62 | 0 | +53.61% | 12 | +0.00% | 0 | +27.86% |
| HBL | 132 | 18 | −4.55% | 7 | −1.97% | 3 | −7.91% |
| LUCK | 142 | 17 | +1.25% | 4 | −5.53% | 2 | −2.72% |
| MARI | 91 | 7 | −5.24% | 15 | −0.62% | 1 | −10.55% |
| MCB | 129 | 4 | +1.08% | 6 | +14.52% | 3 | +8.35% |
| MEBL | 141 | 3 | +5.19% | 3 | +12.59% | 2 | +8.93% |
| OGDC | 137 | 3 | +24.16% | 5 | +3.95% | 1 | +23.69% |
| PPL | 133 | 12 | +20.39% | 5 | +2.84% | 3 | +23.39% |
| PSO | 101 | 17 | −20.08% | 17 | +2.23% | 5 | −23.41% |
| UBL | 109 | 24 | +23.82% | 9 | −6.85% | 2 | +10.37% |

**ENGRO standalone** (disjoint 2024 window, 95 test rows — reported but not
in the sleeve): Ungated **+53.61%** (Sharpe +3.88, maxDD −7.07%, 12 trades)
vs Buy & Hold +27.86% (Sharpe +1.96, maxDD −13.16%). Gated made 0 trades
(no ENGRO test row cleared the 0.55 gate) → 0.00%. ENGRO's large ungated
number rides a strong 2024 run its UP calls happened to catch; it is a
single ticker on a short, non-overlapping window and should **not** be read
as representative — precisely why it is quarantined from the headline.

### No-leakage proof (test-split only)

The backtest reads only `test.parquet` and asserts, per ticker, that
`test_min_date > val_max_date`. Side-by-side with the pre-existing
`verify_dataset.py` (which independently derives the split from
`build_ml_dataset.py`'s chronological logic) — the `val_max` / `test_min`
boundaries match exactly:

| Ticker | verify_dataset val_max | verify_dataset test_min | backtest val_max | backtest test_min | test_max | test rows |
|---|---|---|---|---|---|---:|
| ENGRO | 2024-08-13 | 2024-08-15 | 2024-08-13 | 2024-08-15 | 2024-12-27 | 95 |
| HBL | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| LUCK | 2025-10-24 | 2025-10-27 | 2025-10-24 | 2025-10-27 | 2026-05-29 | 147 |
| MARI | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| MCB | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| MEBL | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| OGDC | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| PPL | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| PSO | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |
| UBL | 2025-10-23 | 2025-10-24 | 2025-10-23 | 2025-10-24 | 2026-05-29 | 148 |

`test_min > val_max` for all 10 tickers → the backtest trades strictly on
future-of-validation rows. No train or val row is ever traded.

### Judgment calls (so the number can be trusted or challenged)

1. **Signal → trade rule = "regime hold", not overlapping fixed-5-day
   holds.** The label is a 5-day-ahead direction, so the literal rule is
   "buy at close, sell 5 closes later" per UP day. But UP signals fire on
   consecutive days; honoring each as its own 5-day trade needs several
   concurrent positions in one ticker = pyramiding/leverage, which the
   requirements forbid. So the rule holds **one** long position while the
   model stays bullish (gated: bullish *and* confident) and exits at the
   close when it flips. This is the standard non-pyramiding translation and
   is what "one position per signal" means in a book with no leverage.
2. **Gated = the production reality.** `max_prob > 0.55` matches
   `Arbitrator.ML_GATE`. On the single latest day per ticker (what live
   production probes), no ticker clears it — but across the 1,426 historical
   test days, 105 do (7.4%), so the gated backtest is not empty. It trades
   less, earns less, and draws down far less than ungated.
3. **CGT modelled post-hoc, not per-trade** (see cost section) — folding a
   gains-only tax into per-trade fees would wrongly penalise losers.
4. **Buy-and-hold pays entry fees only** (a holder doesn't churn); its lower
   cost is a legitimate structural advantage and is reported as-is.
5. **ENGRO quarantined** from the sleeve because its test window doesn't
   overlap the others'; stitching it in would inject ~10 months of flat-cash
   days and distort the annualised Sharpe. It is still fully reported.

### Honest reading of the result

The ungated strategy beat buy-and-hold on return (+5.10% vs +3.33%) and
Sharpe (0.43 vs 0.33) over this ~7-month out-of-sample window, net of a
conservative 0.30% round-trip cost — but the edge is thin, the sleeve Sharpe
is well below 1.0, the max drawdown (−21%) is nearly as deep as buy-and-hold,
and the per-ticker spread is wide (PSO −20% to UBL +24%). The gated version's
main virtue is risk reduction (−5.70% drawdown) from being in cash most of
the time, not return. This is one window, one universe, one model — it
validates that the signal is *not worthless* after costs, and does **not**
establish a deployable trading strategy. Next steps that would move the
needle: retrain on a larger/deeper history, fix the structural FLAT-blindness,
and backfill ENGRO's missing history so it can join a single common window.
