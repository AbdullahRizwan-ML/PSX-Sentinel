"""
PSX Sentinel — XGBoost price-direction model training (Phase 3 Session 2).

Loads train/val/test.parquet (built by scripts/build_ml_dataset.py with the
split-adjustment fix applied), fits a multi-class XGBoost classifier on
the FEATURE_COLUMNS exported from app.ml.features, uses the val split for
early stopping only, and evaluates on the held-out test split.

Why XGBoost specifically (not LightGBM):
    The training set is small (~6,600 rows). LightGBM's leaf-wise tree
    growth tends to overfit small tabular datasets without careful
    tuning. XGBoost's defaults are more forgiving for a first pass. A
    second model (LightGBM, or an ensemble) is only worth revisiting if
    this evaluation comes back weak enough to justify the added
    complexity.

Reproducibility:
    A single fixed RANDOM_SEED is set for numpy, the XGBoost trainer,
    and the shuffle on training data (we do NOT shuffle val or test,
    those stay in chronological row order).

Phase 7 Session 1 — feature-set switch:
    `--features base` (default) trains the 11-column production set and
    writes ml_data/model.json, exactly as before. `--features extended`
    trains the same architecture on those 11 columns PLUS the two
    experimental sector-flow columns, and MUST be pointed at a
    different artifact via --model-out. The production artifact is
    never silently replaced by a model whose input shape the live
    inference path (app/ml/inference.py builds an 11-value vector from
    prices alone) cannot produce.

Usage (from backend/ with venv active):
    python scripts/train_ml_model.py
    python scripts/train_ml_model.py --features extended \
        --model-out ml_data/model_flow.json \
        --metrics-out ml_data/metrics_flow.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    classification_report,
    confusion_matrix,
)

from app.ml.features import (  # noqa: E402
    EXTENDED_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
)

RANDOM_SEED = 42
ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"
MODEL_PATH = ML_DATA / "model.json"
METRICS_PATH = ML_DATA / "metrics.json"

FEATURE_SETS = {
    "base": FEATURE_COLUMNS,
    "extended": EXTENDED_FEATURE_COLUMNS,
}

# UP / DOWN / FLAT, fixed order — must match downstream inference code.
LABEL_TO_INT = {"DOWN": 0, "FLAT": 1, "UP": 2}
INT_TO_LABEL = {v: k for k, v in LABEL_TO_INT.items()}
CLASS_NAMES = ["DOWN", "FLAT", "UP"]


def _load_split(name: str) -> pd.DataFrame:
    path = ML_DATA / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run scripts/build_ml_dataset.py first."
        )
    return pd.read_parquet(path)


def _xy(
    df: pd.DataFrame, feature_columns: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Extract feature matrix and integer label vector."""
    missing = [c for c in feature_columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"Split is missing feature column(s) {missing} - rebuild "
            f"with scripts/build_ml_dataset.py."
        )
    x = df[feature_columns].astype(float).to_numpy()
    y = df["label"].map(LABEL_TO_INT).to_numpy()
    if np.isnan(x).any():
        raise ValueError(
            "Feature matrix contains NaN — dataset build should have "
            "dropped these rows. Investigate before retraining."
        )
    return x, y


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--features",
        choices=sorted(FEATURE_SETS),
        default="base",
        help="'base' = the 11 production columns (default); 'extended' "
             "= those plus the Phase 7 S1 sector-flow columns",
    )
    ap.add_argument("--model-out", default=None)
    ap.add_argument("--metrics-out", default=None)
    args = ap.parse_args()

    feature_columns = FEATURE_SETS[args.features]
    model_path = Path(args.model_out) if args.model_out else MODEL_PATH
    metrics_path = (
        Path(args.metrics_out) if args.metrics_out else METRICS_PATH
    )
    if args.features != "base" and model_path == MODEL_PATH:
        raise SystemExit(
            "Refusing to overwrite ml_data/model.json with a "
            f"'{args.features}' model: app/ml/inference.py feeds it an "
            f"{len(FEATURE_COLUMNS)}-value vector built from prices "
            "alone and cannot supply the extra columns. Pass "
            "--model-out with a different path."
        )

    np.random.seed(RANDOM_SEED)

    train_df = _load_split("train")
    val_df = _load_split("val")
    test_df = _load_split("test")

    print(f"Loaded: train={len(train_df):,}  "
          f"val={len(val_df):,}  test={len(test_df):,}")
    print(f"Feature set: '{args.features}' "
          f"({len(feature_columns)} columns)")
    print(f"Feature columns: {feature_columns}")
    print(f"Model out:   {model_path}")
    print(f"Metrics out: {metrics_path}")
    print(f"Random seed: {RANDOM_SEED}")
    print()

    # Shuffle train only; val/test stay in chronological order.
    train_df = train_df.sample(
        frac=1.0, random_state=RANDOM_SEED
    ).reset_index(drop=True)

    x_tr, y_tr = _xy(train_df, feature_columns)
    x_va, y_va = _xy(val_df, feature_columns)
    x_te, y_te = _xy(test_df, feature_columns)

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=800,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        min_child_weight=5,
        tree_method="hist",
        eval_metric="mlogloss",
        early_stopping_rounds=30,
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )

    print("Fitting XGBoost (early stopping on val mlogloss)…")
    model.fit(
        x_tr,
        y_tr,
        eval_set=[(x_tr, y_tr), (x_va, y_va)],
        verbose=False,
    )
    best_iter = model.best_iteration
    print(f"Best iteration on val: {best_iter} "
          f"(of {model.n_estimators} max)")
    print()

    # ── TEST-SET EVALUATION (final reported numbers) ─────────────────────
    y_pred = model.predict(x_te)
    acc = accuracy_score(y_te, y_pred)
    report_dict = classification_report(
        y_te,
        y_pred,
        labels=[LABEL_TO_INT[c] for c in CLASS_NAMES],
        target_names=CLASS_NAMES,
        digits=4,
        output_dict=True,
        zero_division=0,
    )
    cm = confusion_matrix(
        y_te, y_pred, labels=[LABEL_TO_INT[c] for c in CLASS_NAMES]
    )

    print("=" * 78)
    print("TEST-SET METRICS (final reported, never used for tuning)")
    print("=" * 78)
    naive_up = float((y_te == LABEL_TO_INT["UP"]).mean())
    print(f"Accuracy: {acc:.4f}  (random-chance baseline = 0.3333)")
    print(f"Always-UP naive baseline on this test set: {naive_up:.4f}  "
          f"-> model is {'ABOVE' if acc > naive_up else 'BELOW'} it "
          f"({(acc - naive_up) * 100:+.2f}pp)")
    print()
    print(
        classification_report(
            y_te,
            y_pred,
            labels=[LABEL_TO_INT[c] for c in CLASS_NAMES],
            target_names=CLASS_NAMES,
            digits=4,
            zero_division=0,
        )
    )

    print("Confusion matrix (rows = actual, cols = predicted):")
    print(f"            {CLASS_NAMES[0]:>8} {CLASS_NAMES[1]:>8} "
          f"{CLASS_NAMES[2]:>8}")
    for i, row_name in enumerate(CLASS_NAMES):
        print(
            f"  {row_name:<6}    "
            + " ".join(f"{v:>8d}" for v in cm[i])
        )
    print()

    # Per-class proportion of predictions, as a sanity check that the
    # model isn't simply collapsing to the majority class.
    pred_counts = pd.Series(
        [INT_TO_LABEL[int(p)] for p in y_pred]
    ).value_counts()
    print("Test prediction distribution:")
    for c in CLASS_NAMES:
        n = int(pred_counts.get(c, 0))
        print(f"  {c:<5}: {n:>5}  ({n / len(y_pred) * 100:5.1f}%)")
    print()

    print("Feature importances (gain):")
    importances = sorted(
        zip(feature_columns, model.feature_importances_.tolist()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    for name, gain in importances:
        bar = "#" * int(round(gain * 80))
        print(f"  {name:<18} {gain:6.4f}  {bar}")
    print()

    model.save_model(str(model_path))
    print(f"Saved trained model -> {model_path}")

    metrics = {
        "random_seed": RANDOM_SEED,
        "feature_set": args.features,
        "best_iteration": int(best_iter),
        "n_train": int(len(train_df)),
        "n_val": int(len(val_df)),
        "n_test": int(len(test_df)),
        "feature_columns": feature_columns,
        "class_names": CLASS_NAMES,
        "test_accuracy": float(acc),
        "test_accuracy_naive_always_up": naive_up,
        "test_classification_report": report_dict,
        "test_confusion_matrix": cm.tolist(),
        "feature_importances": [
            {"feature": n, "importance": float(g)} for n, g in importances
        ],
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"Saved metrics       -> {metrics_path}")


if __name__ == "__main__":
    main()
