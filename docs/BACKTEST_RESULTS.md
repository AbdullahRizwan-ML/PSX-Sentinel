# Backtest Results

> Home for out-of-sample trading backtests of PSX Sentinel's models. Each
> run records the model artifact, the exact split window, the cost
> assumption, and the literal result numbers vs a buy-and-hold benchmark.
> Reproduce with `python backend/scripts/backtest_xgboost.py` (read-only —
> no DB writes, no retrain). When a model is retrained, append a new dated
> section rather than overwriting; keep the old numbers so regressions are
> visible.

---

## Phase 7 Session 1 — sector FIPI/LIPI flow as an ML feature: retrain + backtest (2026-09-10)

**One-line answer:** giving the model the sector institutional-flow
imbalance ratio (plus a `flow_available` flag) **did not help — it very
slightly hurt.** Test accuracy fell **43.19% → 42.26%** and the ungated
sleeve return fell **+18.36% → +15.41%** (Sharpe 1.12 → 0.99) on the
identical test window. The drop is not statistically significant
(McNemar p = 0.19) and sits inside seed noise, so the honest verdict is
**"does nothing, if anything mildly negative"** — but the direction is
consistent: across 7 seeds the flow model won **1 of 7**, mean delta
**−0.52pp**. The mechanism is visible and worth recording: the feature
carries a **strong relationship in the training period that inverts in
the test period**, so the model learns something real that then stops
being true.

### Scope — what this session tested and what it did NOT

| | |
|---|---|
| Tested | Two extra features: `sector_flow_ratio` (raw Σnet/Σgross over the last ≤10 flow trading days for the ticker's mapped NCCPL sector(s)) and `flow_available` (0/1) |
| Not tested | News, filing, and fundamentals terms — still excluded. News is a rolling recent-only mirror (structurally dead over this window, Phase 6 Session 2); `company_fundamentals` is a current-snapshot table with no history, so a historical join would be lookahead bias (Phase 6 Session 1). **This session did not attempt to close either gap** |
| Not touched | `arbitrator.py`. Nothing was wired into production scoring — the flow term keeps its hand-picked ±10 mapping. This validates the idea first, the same build → evaluate → gate-and-wire sequence the original ML model went through over three sessions |
| Production artifact | `ml_data/model.json` **deliberately left in place.** A 13-feature model cannot be served by the live path — `app/ml/inference.py` builds an 11-value vector from prices alone — so swapping it in would have been a silent production break, not an experiment. The trainer now refuses that overwrite outright. New artifact: `model_flow_phase7s1.json` |
| LLM calls | **Zero.** Both new features are pure numbers; nothing here needs an agent |

### The comparison is genuinely one-variable

The dataset was rebuilt from the live DB, and the rebuild is provably a
pure superset of the Session 6 one:

| Check | Result |
|---|---|
| Row counts | 10,050 labeled (train 7,034 / val 1,504 / test 1,512) — identical to Session 6 |
| 16 base columns (date/ticker/close + 11 features + forward return + label) | **Byte-identical** to the pre-rebuild parquet (`pandas.testing.assert_frame_equal`, `check_exact=True`) on all three splits |
| Added columns | 2 features + 4 audit columns, nothing removed |
| Per-ticker split boundaries | Identical to Session 6 for all 10 tickers; `train_max < val_min < val_max < test_min` holds for all 10 |
| Control retrain (11 features, same rebuilt data, seed=42) | **43.19%, best iteration 34 — reproduces the Session 6 model exactly, and the saved artifact is SHA-256-identical to production `model.json`** |

That last row matters: the control isn't "close to" the documented
baseline, it *is* the baseline, bit for bit. So every difference reported
below is attributable to the two added columns and nothing else.

### Point-in-time safety — proven, not asserted

The builder fetches each sector's full flow series once and slices the
per-row 10-day window in pandas (~10,000 per-row SQL round trips would be
unusable). That shortcut was verified rather than trusted, by
`backend/scripts/verify_flow_features.py`:

| Check | Result |
|---|---|
| Per-row SQL equivalence — re-ran production `AnalysisOrchestrator._SECTOR_FLOW_SQL` with `report_date` = that row's own date, recomputed the ratio and gate from the returned rows | **60/60 exact match** (tolerance 1e-12) |
| Per-row point-in-time — every flow date the SQL returned is ≤ the row's date, asserted against the returned rows, not inferred from the `WHERE` clause | **60/60** |
| Whole-dataset point-in-time — every row's recorded `flow_window_end` ≤ its own date | **0 violations of 9,086 windowed rows** |
| Max staleness observed | **0 days** — every labeled row date has a same-day flow row, so the 14-day staleness gate never fired inside the dataset |

The feature reuses production's constants verbatim
(`NCCPL_SECTOR_MAP`, `FLOW_LOOKBACK_DAYS`, `LIPI_RETAIL_TYPES`,
`_SECTOR_FLOW_SQL`, `Arbitrator.FLOW_MIN_DAYS/FLOW_STALE_DAYS`) — the
sector mapping and flow-window math were not re-derived.

### Before/after — model accuracy (same 1,512 test rows, 2025-11-27 → 2026-07-10)

| Metric | Base (11 features) | Extended (13 features) | Δ |
|---|---:|---:|---:|
| **Test accuracy** | **43.19%** | **42.26%** | **−0.93pp** |
| Always-UP naive baseline | 40.81% | 40.81% | — |
| vs naive baseline | +2.38pp | +1.46pp | −0.92pp |
| Random-chance baseline | 33.33% | 33.33% | — |
| Best iteration (early stop on val) | 34 | 33 | — |
| DOWN recall | 0.2013 | 0.1914 | −0.010 |
| UP recall | 0.8590 | 0.8395 | −0.020 |
| FLAT predictions (of 1,512) | 3 | 12 | +9 |
| UP share of predictions | 82.1% | 81.5% | — |

Hyperparameters, seed, split, and early-stopping rule are identical; only
the feature matrix differs.

### Feature importance of the two new columns

| Rank (of 13) | Feature | Gain |
|---:|---|---:|
| 1 | `price_vs_ma20` | 0.1023 |
| **2** | **`flow_available`** | **0.0895** |
| 3 | `position_52w` | 0.0858 |
| 4 | `rsi_14` | 0.0786 |
| 5 | `return_3m` | 0.0782 |
| 6 | `volume_vs_avg20` | 0.0763 |
| 7 | `price_vs_ma50` | 0.0753 |
| **8** | **`sector_flow_ratio`** | **0.0746** |
| 9–13 | `ma_50`, `volatility_20d`, `ma_20`, `return_1w`, `return_1m` | 0.0637–0.0704 |

**This is not the "near-zero importance" outcome.** The model spent 16.4%
of its total gain on the two new columns — it used them heavily — and
still came out slightly *worse*. That combination (high in-sample
importance, no out-of-sample gain) is the signature of a feature whose
relationship does not hold forward, which the next section confirms
directly.

Two caveats on that importance number:

1. **`flow_available` is a perfect ENGROH indicator in this dataset**, not
   a general "no data" flag. Flow coverage is 100% for all 9 mapped
   tickers (9,086 rows) and 0% for ENGROH (964 rows, sector "Investment
   Companies" is deliberately unmapped from NCCPL). So its #2 gain rank
   is ambiguous between "this row has no flow reading" and "this row is
   ENGROH" — the model may simply have found a ticker dummy. Excluding
   ENGROH's rows entirely, the extended model is still worse (see below),
   so this does not rescue the result.
2. **There are only 3 distinct flow series across 10 tickers.** NCCPL's
   finest granularity is sector level, never per-ticker, so
   HBL/MCB/MEBL/UBL share one identical series, MARI/OGDC/PPL/PSO share a
   second, LUCK has a third, and ENGROH has none. The 9,086 rows carrying
   this feature are ~3 independent time series, not 9 — the effective
   sample for learning it is far smaller than the row count suggests.

### Is the −0.93pp real, or noise?

| Test | Result |
|---|---|
| **McNemar exact test** (seed=42 pair, same test rows): 57 rows base-right/ext-wrong vs 43 base-wrong/ext-right, 100 discordant | **p = 0.1933 — not significant** |
| **7 seeds, paired** (42, 0, 1, 2, 3, 7, 13) | base mean **43.43%** (sd 0.58pp), extended mean **42.91%** (sd 0.68pp), mean delta **−0.52pp** (sd 0.71pp), extended wins **1/7** |
| **Rows with `flow_available == 1` only** (145 ENGROH rows dropped, n=1,367) | base 43.09% vs extended 42.06%, **−1.02pp, McNemar p = 0.1797 — not significant** |

Per-seed deltas (extended − base, pp): −0.93, −1.06, −0.20, −0.46,
**+0.73**, −0.26, −1.46.

So: the magnitude is inside run-to-run variation and no single comparison
clears p<0.05, but 6 of 7 seeds and both the accuracy and the backtest
point the same way. **The defensible claim is "no improvement", with a
mild negative lean — not "a significant degradation".**

### Why — the relationship inverts out of sample

This is the substantive finding. Terciles of `sector_flow_ratio` cut
*within each split*, mean forward 5-day return (`flow_available == 1`
rows only):

| Split | n | low tercile | mid | high tercile | high − low |
|---|---:|---:|---:|---:|---:|
| train | 6,360 | +0.3177% | +0.6671% | **+1.4823%** | **+1.16pp** |
| val | 1,359 | +1.5132% | +0.5750% | +1.5379% | +0.02pp |
| test | 1,367 | +1.0855% | +0.6841% | **−0.8381%** | **−1.92pp** |

UP-label rate by the same terciles:

| Split | low | mid | high |
|---|---:|---:|---:|
| train | 37.5% | 40.8% | **44.1%** |
| val | 50.3% | 39.4% | 43.3% |
| test | 44.8% | 44.3% | **32.1%** |

Pearson correlation of `sector_flow_ratio` with `forward_return_5d`:

| Scope | n | r |
|---|---:|---:|
| all splits | 9,086 | +0.0334 |
| train | 6,360 | +0.0666 |
| val | 1,359 | −0.0193 |
| test | 1,367 | **−0.1040** |

In the training period the story works exactly as the Arbitrator's
hand-picked term assumes — heavier net institutional buying precedes
better 5-day returns, monotonically across terciles. In the validation
period the effect vanishes. In the test period it **reverses**: the
highest-inflow tercile has the *worst* forward returns and the *lowest*
UP rate. A pooled all-splits correlation of +0.033 would have looked
mildly encouraging and is, on this evidence, an artifact of the training
period dominating the pool.

### Before/after — backtest (identical methodology, same test window)

Long-only regime-hold, close-to-close, equal-weight, one position/ticker,
no leverage, `vectorbt==1.0.0`, 0.15%/side commission (0.30% round trip),
15% CGT post-hoc applied identically to B&H, slippage not modelled. Same
harness (`backtest_xgboost.py`, now with `--model`/`--features`) — the
baseline column below was re-run this session and reproduced Session 6's
published numbers exactly.

| Metric | Base ungated | **Ext ungated** | Base gated | **Ext gated** | Buy & Hold |
|---|---:|---:|---:|---:|---:|
| Total return (pre-CGT) | **+18.36%** | +15.41% | +5.57% | +5.44% | +13.81% |
| Total return (post-CGT) | +15.60% | +13.10% | +4.74% | +4.62% | +11.74% |
| Ann. Sharpe | **+1.12** | +0.99 | +0.99 | +0.92 | +0.81 |
| Max drawdown | −19.37% | **−19.19%** | −4.30% | **−4.10%** | −21.79% |
| Win rate | 61.43% | 61.46% | 69.23% | 68.00% | n/a |
| Trades | 70 | **96** | 26 | 25 | 0 |
| Excess return vs B&H (pre-CGT) | **+4.55pp** | +1.60pp | −8.24pp | −8.37pp | — |
| Excess Sharpe vs B&H | **+0.31** | +0.18 | +0.18 | +0.11 | — |
| Test rows clearing the 0.55 gate | 99 / 1,512 | 103 / 1,512 | — | — | — |

**Cost sensitivity (0.05%/side discount broker):** extended ungated
+17.69% pre-CGT / +15.04% post / Sharpe +1.10 / max DD −18.95% / win
63.54% — vs base's +20.09% / +17.08% / +1.20 / −19.17%. The ordering is
unchanged at lower cost, so this is not a fee artifact.

The extended model trades **96 times vs 70** for less return — it flips
regime more often without being more right, which is exactly how a noisy
extra input costs money.

### Per-ticker (shared window; ENGROH enters 2025-12-08)

| Ticker | Base #UP | Ext #UP | Base ungated | Ext ungated | Base trades | Ext trades | Buy & Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| ENGROH | 140 | 134 | +39.17% | +39.04% | 5 | 8 | +29.87% |
| HBL | 148 | 147 | −9.01% | **+3.55%** | 4 | 5 | +2.99% |
| LUCK | 131 | 134 | −2.56% | −8.65% | 10 | 10 | +3.64% |
| MARI | 33 | 49 | +23.56% | +16.67% | 20 | 26 | −3.61% |
| MCB | 151 | 140 | +18.44% | +18.74% | 2 | 9 | +17.85% |
| MEBL | 149 | 144 | +14.25% | +15.26% | 4 | 5 | +29.81% |
| OGDC | 150 | 145 | +32.63% | +34.36% | 3 | 6 | +31.67% |
| PPL | 136 | 134 | +22.50% | **+4.93%** | 7 | 9 | +19.04% |
| PSO | 93 | 98 | −14.82% | −16.09% | 8 | 8 | −21.49% |
| UBL | 110 | 108 | +59.77% | +46.60% | 14 | 17 | +28.65% |

Per-ticker results move in both directions (HBL +12.6pp better, PPL
−17.6pp worse) — more evidence that what changed is noise placement, not
a systematic edge.

### No-leakage proof (test-split only)

Unchanged from Session 6 and re-asserted on both runs: `test_min >
val_max` per ticker, `True` for all 10, cross-checked against
`verify_dataset.py`'s independently derived boundaries (identical). The
5-day forward label remains a training target only, never a trading
input. The two new features add a *third* leakage surface — a flow window
reaching past the row's own date — and that is separately proven above
(0 violations of 9,086 windowed rows, plus 60/60 per-row re-fetches
against production SQL).

### Honest verdict

**It does nothing. Report it as a negative result and do not wire it in.**

1. **Accuracy: no improvement, mild negative lean.** 43.19% → 42.26% at
   seed=42; −0.52pp averaged over 7 seeds; extended wins 1 of 7. No
   comparison reaches p<0.05, so calling this "worse" would overstate it
   — but there is no version of this result in which the feature helped.
2. **Trading: worse where it counts.** Excess return over buy-and-hold
   fell from +4.55pp to +1.60pp and excess Sharpe from +0.31 to +0.18,
   with 37% more trades. Drawdown improved trivially (−19.37% →
   −19.19%), which does not pay for the return give-up.
3. **The hand-picked ±10 rule is not vindicated by this.** The question
   asked was whether a model could learn a better mapping than the
   Arbitrator's `clamp(ratio/0.125, −1, +1) × 10`. The answer is that
   there was no stable mapping to learn over this window — which, read
   alongside Phase 6 Session 2's finding that the composite score has no
   relationship with forward returns, is *consistent with the flow term
   contributing noise to production scoring today*. This session does not
   prove that (it tested a different functional form on a different
   target), but nothing here supports keeping the term on empirical
   grounds.
4. **The most useful thing learned is the instability, not the accuracy
   number.** Train +1.16pp / val +0.02pp / test −1.92pp tercile spread,
   and a correlation that flips sign from +0.067 to −0.104, is a clean
   demonstration that the flow-to-return relationship is regime-dependent
   over 2022–2026. Anyone tempted to revisit this should test *regime
   stability first* on a wider window, not accuracy on this one.
5. **Two structural limits cap what this experiment could ever have
   shown.** Only 3 distinct flow series exist across the universe
   (NCCPL is sector-level, never per-ticker), and the test window is one
   ~7.4-month strongly-bullish regime — the same single-regime caveat
   that has applied since Phase 5 Session 1. A sector-level feature
   evaluated on 3 series over one regime is weak evidence either way; the
   negative result is credible as "no usable edge here", not as "flow
   data is worthless".
6. **Production is unchanged.** `model.json` is still the 11-feature
   Session 6 artifact, `arbitrator.py` is untouched, and no scoring
   behavior moved. The experimental model exists only as
   `ml_data/model_flow_phase7s1.json`.

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
