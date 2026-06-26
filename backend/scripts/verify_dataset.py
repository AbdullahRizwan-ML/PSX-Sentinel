"""
PSX Sentinel — post-fix dataset sanity check (Phase 3 Session 2, Part 1).

Reads the train/val/test parquet files and:
  1. Reports the most extreme forward_return_5d values per split — should
     no longer contain the ~-88% split-induced outlier.
  2. Re-verifies the per-ticker chronological invariant:
       train_max_date < val_min_date < val_max_date < test_min_date
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"


def main() -> None:
    train = pd.read_parquet(ML_DATA / "train.parquet")
    val = pd.read_parquet(ML_DATA / "val.parquet")
    test = pd.read_parquet(ML_DATA / "test.parquet")

    print("=" * 78)
    print("MOST EXTREME forward_return_5d PER SPLIT (top/bottom 5)")
    print("=" * 78)
    for name, df in [("train", train), ("val", val), ("test", test)]:
        print(f"\n{name}:")
        cols = ["ticker", "date", "close", "forward_return_5d", "label"]
        print("  bottom 5 (most negative):")
        print(
            df.nsmallest(5, "forward_return_5d")[cols]
            .to_string(index=False)
        )
        print("  top 5 (most positive):")
        print(
            df.nlargest(5, "forward_return_5d")[cols]
            .to_string(index=False)
        )

    print()
    print("=" * 78)
    print("PER-TICKER CHRONOLOGICAL INVARIANT")
    print("=" * 78)
    print("(train_max < val_min < val_max < test_min for every ticker)")
    print()
    fail = 0
    for ticker in sorted(train["ticker"].unique()):
        t_tr = train[train["ticker"] == ticker]["date"]
        t_va = val[val["ticker"] == ticker]["date"]
        t_te = test[test["ticker"] == ticker]["date"]
        if t_tr.empty or t_va.empty or t_te.empty:
            print(
                f"  {ticker}: SKIP (one or more splits empty: "
                f"train={len(t_tr)}, val={len(t_va)}, test={len(t_te)})"
            )
            continue
        tr_max = t_tr.max()
        va_min = t_va.min()
        va_max = t_va.max()
        te_min = t_te.min()
        ok = tr_max < va_min < va_max < te_min
        marker = "OK" if ok else "FAIL"
        if not ok:
            fail += 1
        print(
            f"  {ticker:<6} [{marker}]  "
            f"train_max={tr_max.date()}  "
            f"val_min={va_min.date()}  "
            f"val_max={va_max.date()}  "
            f"test_min={te_min.date()}"
        )

    print()
    if fail == 0:
        print("All tickers passed the chronological-split invariant.")
    else:
        print(f"{fail} ticker(s) FAILED the chronological invariant.")


if __name__ == "__main__":
    main()
