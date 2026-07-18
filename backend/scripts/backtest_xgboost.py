"""
PSX Sentinel - Out-of-sample backtest of the XGBoost price-direction model
(Phase 5 Session 1).

WHAT THIS ANSWERS
-----------------
The model has only ever been validated by 3-class test-set accuracy
(39.34% vs 33.33% random - Phase 3 Session 2). Nobody has ever asked:
"if you had actually traded on its historical predictions, what would
your money have done, net of realistic PSX costs?" This script answers
that - honestly, out of sample, close-to-close.

NON-NEGOTIABLES (see docs prompt, Phase 5 Session 1)
----------------------------------------------------
1. Uses the ALREADY-TRAINED model as-is (ml_data/model.json). No retrain.
2. TEST SPLIT ONLY. It reads ml_data/test.parquet - the exact per-ticker
   chronological 70/15/15 test tail produced by build_ml_dataset.py. It
   ALSO loads val.parquet purely to assert, per ticker, that
   test_min_date > val_max_date (no look-ahead contamination). Train/val
   rows are never traded on.
3. Reports TWO trading rules side by side:
     - GATED   : trade only when max(predict_proba) > 0.55, matching
                 Arbitrator.ML_GATE - what the live system would actually
                 have acted on.
     - UNGATED : trade on every UP prediction regardless of confidence -
                 the raw signal quality if the gate were removed.
4. Realistic PSX transaction costs, stated explicitly (see COSTS below).
5. Close-to-close execution ONLY. daily_prices high/low are derived
   max/min(open,close) approximations (docs/KNOWN_ISSUES.md), so no
   intraday stop/limit fills are simulated. Enter and exit at close.
6. Equal-weight, one position per ticker at a time, no leverage, no
   position sizing. This validates the signal; it does not optimise a
   portfolio.

TRADING RULE - judgment calls (documented for posterity)
--------------------------------------------------------
* LONG-ONLY. Retail short-selling on the PSX is impractical (no broad,
  cheap, retail-accessible securities-lending market for KSE-30 names),
  so a DOWN or FLAT prediction maps to "flat / in cash", never to a
  short. This is the honest tradable interpretation of the signal.

* REGIME hold, not overlapping fixed-5-day holds. The model's label is a
  5-trading-day-ahead direction, so the literal rule would be "buy at
  close, sell 5 closes later" for every UP day. But UP signals fire on
  consecutive days, and honoring each as its own 5-day trade would
  require several concurrent positions in the same ticker at once -
  i.e. pyramiding / leverage, which requirement 6 forbids. So instead we
  hold ONE long position while the model stays bullish (gated: bullish
  AND confident) and exit at the close on the day it flips away. This is
  the standard "one position per ticker, no leverage" translation and is
  what "one position per signal" means in a non-pyramiding book.
  accumulate=False in vectorbt enforces the no-scaling-in rule.

* Entries/exits are shifted so a signal computed from day t's close is
  acted on at day t's close within the same bar via vectorbt's default
  (fees applied at the executed close). Because the label/feature vector
  for row t uses only data up to and including t's close, and we execute
  at t's close, there is no forward peeking. (The 5-day forward return
  used for the model's *label* is never used as a trading input here -
  we trade purely off predict_proba, which is a function of the
  backward-looking features only.)

COSTS - the explicit assumption and why
---------------------------------------
Round-trip is charged as two components:

  (A) Brokerage commission + statutory per-trade charges: 0.15% PER SIDE
      (=> 0.30% round trip), applied by vectorbt as `fees` on both the
      entry and the exit close.

      Rationale / sourcing: PSX retail brokerage commission is capped by
      regulation and in practice ranges from ~0.03-0.05% per side at
      online/discount brokers (e.g. app-based brokers) up to the older
      0.15% / 2.5-paisa-per-share conventional-broker rate. On top of
      the bare commission sit FED / provincial sales tax on the
      commission (~13-16% OF the commission), CDC charges, and the
      SECP/PSX/NCCPL levies - each a fraction of a basis point but
      additive. We adopt 0.15% per side as a single conservative bundle
      that covers a mid-to-traditional retail broker plus those add-ons.
      It is deliberately on the expensive side: if the edge survives
      0.30% round-trip it is more likely to survive real life. A
      discount-broker sensitivity (0.05%/side) is printed alongside.

  (B) Capital Gains Tax: 15% of NET realised gain, applied ONCE, post-hoc,
      at the sleeve level - NOT as a per-trade fee. PSX CGT for a tax
      filer on securities held under 12 months is a flat 15% under the
      current regime (all our holds are days long, so short-term). CGT is
      levied on net annual gains, only bites winners, and is offset by
      losses - folding it into per-trade `fees` would wrongly tax losing
      trades and double-count. So we report the pre-CGT figure (the
      trading result) and a post-CGT net (what reaches the investor),
      and we apply the identical 15%-on-net-gain haircut to the
      buy-and-hold benchmark so the comparison stays apples-to-apples.

  SLIPPAGE is NOT separately modelled. Close-to-close fills are assumed
  achievable at the printed close. On thin PSX names this is optimistic;
  it is flagged here and in the results doc as a known simplification.

AGGREGATION - the disjoint-window caveat
----------------------------------------
When a ticker's test window is DISJOINT from the rest (as delisted
ENGRO's Aug-Dec 2024 window was, pre-Session-6), stitching it into one
equity curve would inject months of flat-cash days that distort an
annualised Sharpe - so such a ticker is reported standalone, outside the
headline sleeve. Since the Phase 5 Session 6 retrain (ENGROH replaced
ENGRO in the training universe) every ticker shares one common window
and the sleeve is all of them; the standalone section only renders if
the disjoint ticker is present in test.parquet. (ENGROH's test window
starts 11 days later than the others' - 2025-12-08 vs 2025-11-27, its
series is slightly shorter - which the sleeve handles as flat cash
before entry, same as always. That is an overlap difference, not a
disjoint window.)

USAGE (from backend/ with venv active):
    python scripts/backtest_xgboost.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import vectorbt as vbt  # noqa: E402
import xgboost as xgb  # noqa: E402

from app.ml.features import FEATURE_COLUMNS  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────
ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"
MODEL_PATH = ML_DATA / "model.json"

# Must match train_ml_model.py LABEL_TO_INT ordering exactly.
CLASS_NAMES = ["DOWN", "FLAT", "UP"]

GATE = 0.55                 # matches Arbitrator.ML_GATE (strict >)
COMMISSION_PER_SIDE = 0.0015   # 0.15% per side => 0.30% round trip
DISCOUNT_PER_SIDE = 0.0005     # 0.05% per side sensitivity (online broker)
CGT_RATE = 0.15            # 15% on net realised gain, filer, <12mo hold
INIT_CASH = 10_000.0       # per ticker sleeve leg
TRADING_DAYS = 252         # PSX ≈ 252 sessions/yr, used for Sharpe annualisation

# ENGRO's test window was disjoint from the other tickers' (2024 vs
# 2025-26) back when it was in the dataset - it was reported standalone,
# outside the sleeve. Since the Phase 5 Session 6 retrain the universe is
# ENGROH-based and every ticker shares one common window, so this ticker
# is simply absent from test.parquet and the standalone section is
# skipped. Kept (rather than deleted) so re-running against the archived
# pre-Session-6 parquets still reports correctly.
DISJOINT_TICKER = "ENGRO"


# ── Metric helpers (computed manually from vectorbt equity curves so the
#    annualisation basis is explicit, not hidden in a library default) ─────
def curve_metrics(value: pd.Series) -> dict:
    """total return, annualised Sharpe (sqrt(252)), max drawdown from an
    equity-value curve."""
    value = value.dropna()
    if len(value) < 2 or value.iloc[0] == 0:
        return {"total_return": 0.0, "sharpe": float("nan"), "max_dd": 0.0}
    total_return = value.iloc[-1] / value.iloc[0] - 1.0
    daily = value.pct_change().dropna()
    if daily.std(ddof=1) == 0 or len(daily) < 2:
        sharpe = float("nan")
    else:
        sharpe = (
            daily.mean() / daily.std(ddof=1) * np.sqrt(TRADING_DAYS)
        )
    running_max = value.cummax()
    max_dd = (value / running_max - 1.0).min()
    return {
        "total_return": float(total_return),
        "sharpe": float(sharpe),
        "max_dd": float(max_dd),
    }


def pooled_trade_stats(portfolios: dict) -> dict:
    """Win rate and trade count pooled across a set of per-ticker vectorbt
    portfolios (a trade = a closed round-trip position)."""
    frames = []
    for pf in portfolios.values():
        recs = pf.trades.records_readable
        if len(recs):
            frames.append(recs)
    if not frames:
        return {"n_trades": 0, "win_rate": float("nan")}
    allt = pd.concat(frames, ignore_index=True)
    closed = allt[allt["Status"] == "Closed"]
    n = len(closed)
    if n == 0:
        return {"n_trades": 0, "win_rate": float("nan")}
    wins = int((closed["PnL"] > 0).sum())
    return {"n_trades": int(n), "win_rate": wins / n}


def combined_curve(portfolios: dict) -> pd.Series:
    """Equal-weight sleeve equity curve: sum each ticker's value() reindexed
    onto the union calendar, holding flat cash (INIT_CASH) before a ticker's
    window starts. Sum => one sleeve curve starting at n*INIT_CASH."""
    union = None
    for pf in portfolios.values():
        idx = pf.value().index
        union = idx if union is None else union.union(idx)
    union = union.sort_values()
    total = pd.Series(0.0, index=union)
    for pf in portfolios.values():
        v = pf.value().reindex(union)
        v = v.ffill()                 # after window: hold last value
        v = v.fillna(INIT_CASH)       # before window: flat cash
        total = total + v
    return total


def apply_cgt(value: pd.Series) -> tuple[float, float]:
    """Return (pre_cgt_return, post_cgt_return) for an equity curve.
    CGT bites only a positive net gain."""
    v0, v1 = value.iloc[0], value.iloc[-1]
    pre = v1 / v0 - 1.0
    gain = v1 - v0
    if gain > 0:
        v1_net = v0 + gain * (1.0 - CGT_RATE)
    else:
        v1_net = v1
    post = v1_net / v0 - 1.0
    return float(pre), float(post)


# ── Core backtest per ticker ─────────────────────────────────────────────
def build_signals(
    df: pd.DataFrame, model: xgb.XGBClassifier
) -> pd.DataFrame:
    """Attach predicted_class, max_prob, and the two entry booleans to a
    single ticker's test rows (chronological)."""
    df = df.sort_values("date").reset_index(drop=True)
    proba = model.predict_proba(df[FEATURE_COLUMNS].astype(float).to_numpy())
    max_idx = proba.argmax(axis=1)
    df = df.assign(
        pred=[CLASS_NAMES[i] for i in max_idx],
        max_prob=proba.max(axis=1),
    )
    df["is_up"] = df["pred"] == "UP"
    df["ungated_long"] = df["is_up"]
    df["gated_long"] = df["is_up"] & (df["max_prob"] > GATE)
    return df


def run_strategy(
    df: pd.DataFrame, long_col: str, fees: float
) -> vbt.Portfolio:
    """Regime long-only portfolio for one ticker: long while `long_col` is
    True, flat otherwise. Close-to-close, no pyramiding."""
    close = pd.Series(
        df["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(df["date"]),
    )
    long = pd.Series(df[long_col].to_numpy(bool), index=close.index)
    entries = long & ~long.shift(1, fill_value=False)
    exits = ~long & long.shift(1, fill_value=False)
    return vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        direction="longonly",
        accumulate=False,       # one position, no scaling in
        init_cash=INIT_CASH,
        fees=fees,
        freq="1D",
    )


def run_buy_hold(df: pd.DataFrame, fees: float) -> vbt.Portfolio:
    """Buy at the first test close, hold to the last. Entry fee only (a
    holder doesn't churn) - its lower cost is a legitimate structural
    advantage of B&H and is reported as such."""
    close = pd.Series(
        df["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(df["date"]),
    )
    return vbt.Portfolio.from_holding(
        close, init_cash=INIT_CASH, fees=fees, freq="1D"
    )


# ── No-leakage assertion ─────────────────────────────────────────────────
def assert_no_leakage(test: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    """Per ticker: test_min must be strictly AFTER val_max. Returns a table
    for printing side-by-side with verify_dataset.py."""
    rows = []
    for t in sorted(test["ticker"].unique()):
        te = test[test["ticker"] == t]["date"]
        va = val[val["ticker"] == t]["date"]
        te_min, te_max = te.min(), te.max()
        va_max = va.max() if len(va) else pd.NaT
        ok = (pd.notna(va_max)) and (te_min > va_max)
        rows.append(
            {
                "ticker": t,
                "val_max": va_max,
                "test_min": te_min,
                "test_max": te_max,
                "n_test": len(te),
                "test_after_val": ok,
            }
        )
    return pd.DataFrame(rows)


def _fmt_pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.2f}%"


def _fmt_sharpe(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.2f}"


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"{MODEL_PATH} not found - run scripts/train_ml_model.py first."
        )
    test = pd.read_parquet(ML_DATA / "test.parquet")
    val = pd.read_parquet(ML_DATA / "val.parquet")
    test["date"] = pd.to_datetime(test["date"])
    val["date"] = pd.to_datetime(val["date"])

    model = xgb.XGBClassifier()
    model.load_model(str(MODEL_PATH))

    print("=" * 78)
    print("PSX SENTINEL - XGBoost out-of-sample backtest")
    print("=" * 78)
    print(f"Model            : {MODEL_PATH.name} (loaded as-is, not retrained)")
    print(f"Test rows        : {len(test):,}  across {test['ticker'].nunique()} tickers")
    print(f"Confidence gate  : max_prob > {GATE}  (matches Arbitrator.ML_GATE)")
    print(
        f"Costs            : {COMMISSION_PER_SIDE * 100:.2f}% commission/side "
        f"({COMMISSION_PER_SIDE * 200:.2f}% round-trip) + {int(CGT_RATE * 100)}% CGT on net gain"
    )
    print(f"Execution        : close-to-close, long-only, 1 position/ticker, no leverage")
    print(f"Sharpe basis     : annualised x sqrt({TRADING_DAYS})")
    print()

    # ── 1. No-leakage proof ──────────────────────────────────────────────
    leak = assert_no_leakage(test, val)
    print("-" * 78)
    print("TEST-SPLIT NO-LEAKAGE CHECK  (test_min must be AFTER val_max)")
    print("-" * 78)
    with pd.option_context("display.max_rows", None):
        show = leak.copy()
        for c in ("val_max", "test_min", "test_max"):
            show[c] = show[c].dt.date.astype(str)
        print(show.to_string(index=False))
    all_ok = bool(leak["test_after_val"].all())
    print(f"\nAll tickers test-after-val: {all_ok}")
    if not all_ok:
        raise RuntimeError("LEAKAGE DETECTED - aborting backtest.")
    print()

    # ── 2. Per-ticker predictions + portfolios ───────────────────────────
    common_tickers = [
        t for t in sorted(test["ticker"].unique()) if t != DISJOINT_TICKER
    ]

    gated_pfs: dict[str, vbt.Portfolio] = {}
    ungated_pfs: dict[str, vbt.Portfolio] = {}
    bh_pfs: dict[str, vbt.Portfolio] = {}
    # discount-cost variants (for the sensitivity line)
    ungated_pfs_disc: dict[str, vbt.Portfolio] = {}
    bh_pfs_disc: dict[str, vbt.Portfolio] = {}

    per_ticker_rows = []
    total_gate_rows = 0
    for t in sorted(test["ticker"].unique()):
        sig = build_signals(test[test["ticker"] == t], model)
        total_gate_rows += int(sig["gated_long"].sum())

        g = run_strategy(sig, "gated_long", COMMISSION_PER_SIDE)
        u = run_strategy(sig, "ungated_long", COMMISSION_PER_SIDE)
        bh = run_buy_hold(sig, COMMISSION_PER_SIDE)
        gated_pfs[t] = g
        ungated_pfs[t] = u
        bh_pfs[t] = bh
        ungated_pfs_disc[t] = run_strategy(sig, "ungated_long", DISCOUNT_PER_SIDE)
        bh_pfs_disc[t] = run_buy_hold(sig, DISCOUNT_PER_SIDE)

        gm = curve_metrics(g.value())
        um = curve_metrics(u.value())
        bm = curve_metrics(bh.value())
        per_ticker_rows.append(
            {
                "ticker": t,
                "n_up": int(sig["is_up"].sum()),
                "n_gate": int(sig["gated_long"].sum()),
                "ung_ret": um["total_return"],
                "ung_tr": int(u.trades.count()),
                "gat_ret": gm["total_return"],
                "gat_tr": int(g.trades.count()),
                "bh_ret": bm["total_return"],
            }
        )

    print("-" * 78)
    print("PER-TICKER (each on its OWN test window; * = disjoint 2024 window)")
    print("-" * 78)
    hdr = (
        f"{'Ticker':<7} {'#UP':>4} {'#gate':>5} "
        f"{'Ungated':>9} {'trades':>6} {'Gated':>9} {'trades':>6} {'Buy&Hold':>9}"
    )
    print(hdr)
    for r in per_ticker_rows:
        star = "*" if r["ticker"] == DISJOINT_TICKER else " "
        print(
            f"{r['ticker']:<6}{star} {r['n_up']:>4} {r['n_gate']:>5} "
            f"{_fmt_pct(r['ung_ret']):>9} {r['ung_tr']:>6} "
            f"{_fmt_pct(r['gat_ret']):>9} {r['gat_tr']:>6} "
            f"{_fmt_pct(r['bh_ret']):>9}"
        )
    print(f"\nTotal test rows clearing the {GATE} gate: {total_gate_rows}")
    print()

    # ── 3. Headline sleeve aggregate (9 common-window tickers) ────────────
    def sleeve_report(label: str, pfs: dict, fees_note: str) -> dict:
        sub = {t: pfs[t] for t in common_tickers}
        curve = combined_curve(sub)
        m = curve_metrics(curve)
        stats = pooled_trade_stats(sub)
        pre, post = apply_cgt(curve)
        print(f"  {label}  ({fees_note})")
        print(f"      total return (pre-CGT) : {_fmt_pct(m['total_return'])}")
        print(f"      total return (post-CGT): {_fmt_pct(post)}")
        print(f"      annualised Sharpe      : {_fmt_sharpe(m['sharpe'])}")
        print(f"      max drawdown           : {_fmt_pct(m['max_dd'])}")
        print(f"      win rate               : {_fmt_pct(stats['win_rate']) if not np.isnan(stats['win_rate']) else 'n/a'}")
        print(f"      trades                 : {stats['n_trades']}")
        print()
        return {
            "label": label,
            "pre": m["total_return"],
            "post": post,
            "sharpe": m["sharpe"],
            "max_dd": m["max_dd"],
            "win_rate": stats["win_rate"],
            "n_trades": stats["n_trades"],
        }

    n_sleeve = len(common_tickers)
    sleeve_min = test[test["ticker"].isin(common_tickers)]["date"].min().date()
    sleeve_max = test[test["ticker"].isin(common_tickers)]["date"].max().date()
    print("=" * 78)
    print("HEADLINE SLEEVE AGGREGATE")
    print(f"  {n_sleeve} equal-weight tickers over the shared window "
          f"{sleeve_min} -> {sleeve_max}")
    if DISJOINT_TICKER in test["ticker"].unique():
        print(f"  ({DISJOINT_TICKER} excluded - disjoint window; "
              f"see per-ticker table)")
    print(f"  Sleeve capital = {n_sleeve} x ${INIT_CASH:,.0f} "
          f"= ${n_sleeve*INIT_CASH:,.0f}")
    print("=" * 78)
    print()
    sleeve = {}
    sleeve["ungated"] = sleeve_report(
        "UNGATED  (long every UP prediction)", ungated_pfs,
        f"{COMMISSION_PER_SIDE*100:.2f}%/side"
    )
    sleeve["gated"] = sleeve_report(
        "GATED    (long only when max_prob > 0.55)", gated_pfs,
        f"{COMMISSION_PER_SIDE*100:.2f}%/side"
    )
    sleeve["buyhold"] = sleeve_report(
        f"BUY & HOLD benchmark (all {n_sleeve}, equal weight)", bh_pfs,
        f"{COMMISSION_PER_SIDE*100:.2f}%/side entry"
    )

    print("-" * 78)
    print("COST SENSITIVITY - discount broker (0.05%/side) instead of 0.15%/side")
    print("-" * 78)
    print()
    sleeve_report(
        "UNGATED  @ 0.05%/side", ungated_pfs_disc, "0.05%/side"
    )
    sleeve_report(
        "BUY & HOLD @ 0.05%/side", bh_pfs_disc, "0.05%/side entry"
    )

    # ── 4. Disjoint-window ticker standalone (skipped when absent) ───────
    if DISJOINT_TICKER in ungated_pfs:
        print("-" * 78)
        print(f"{DISJOINT_TICKER} - standalone (disjoint test window, "
              f"not in sleeve)")
        print("-" * 78)
        for label, pf in [
            ("Ungated", ungated_pfs[DISJOINT_TICKER]),
            ("Gated", gated_pfs[DISJOINT_TICKER]),
            ("Buy&Hold", bh_pfs[DISJOINT_TICKER]),
        ]:
            m = curve_metrics(pf.value())
            print(
                f"  {label:<9} return {_fmt_pct(m['total_return']):>9}  "
                f"Sharpe {_fmt_sharpe(m['sharpe']):>7}  "
                f"maxDD {_fmt_pct(m['max_dd']):>9}  "
                f"trades {int(pf.trades.count())}"
            )
        print()
    print("Done.")


if __name__ == "__main__":
    main()
