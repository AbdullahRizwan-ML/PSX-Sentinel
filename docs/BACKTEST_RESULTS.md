# Backtest Results

> Home for out-of-sample trading backtests of PSX Sentinel's models. Each
> run records the model artifact, the exact split window, the cost
> assumption, and the literal result numbers vs a buy-and-hold benchmark.
> Reproduce with `python backend/scripts/backtest_xgboost.py` (read-only —
> no DB writes, no retrain). When a model is retrained, append a new dated
> section rather than overwriting; keep the old numbers so regressions are
> visible.

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
