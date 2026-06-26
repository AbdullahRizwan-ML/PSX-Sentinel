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

Usage (from backend/ with .venv active):
    python scripts/train_ml_model.py
"""

from __future__ import annotations

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

from app.ml.features import FEATURE_COLUMNS  # noqa: E402

RANDOM_SEED = 42
ML_DATA = Path(__file__).resolve().parent.parent / "ml_data"
MODEL_PATH = ML_DATA / "model.json"
METRICS_PATH = ML_DATA / "metrics.json"

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


def _xy(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Extract feature matrix and integer label vector."""
    x = df[FEATURE_COLUMNS].astype(float).to_numpy()
    y = df["label"].map(LABEL_TO_INT).to_numpy()
    if np.isnan(x).any():
        raise ValueError(
            "Feature matrix contains NaN — dataset build should have "
            "dropped these rows. Investigate before retraining."
        )
    return x, y


def main() -> None:
    np.random.seed(RANDOM_SEED)

    train_df = _load_split("train")
    val_df = _load_split("val")
    test_df = _load_split("test")

    print(f"Loaded: train={len(train_df):,}  "
          f"val={len(val_df):,}  test={len(test_df):,}")
    print(f"Feature columns ({len(FEATURE_COLUMNS)}): {FEATURE_COLUMNS}")
    print(f"Random seed: {RANDOM_SEED}")
    print()

    # Shuffle train only; val/test stay in chronological order.
    train_df = train_df.sample(
        frac=1.0, random_state=RANDOM_SEED
    ).reset_index(drop=True)

    x_tr, y_tr = _xy(train_df)
    x_va, y_va = _xy(val_df)
    x_te, y_te = _xy(test_df)

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
    print(f"Accuracy: {acc:.4f}  (random-chance baseline = 0.3333)")
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
        zip(FEATURE_COLUMNS, model.feature_importances_.tolist()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    for name, gain in importances:
        bar = "#" * int(round(gain * 80))
        print(f"  {name:<18} {gain:6.4f}  {bar}")
    print()

    model.save_model(str(MODEL_PATH))
    print(f"Saved trained model -> {MODEL_PATH}")

    metrics = {
        "random_seed": RANDOM_SEED,
        "best_iteration": int(best_iter),
        "n_train": int(len(train_df)),
        "n_val": int(len(val_df)),
        "n_test": int(len(test_df)),
        "feature_columns": FEATURE_COLUMNS,
        "class_names": CLASS_NAMES,
        "test_accuracy": float(acc),
        "test_classification_report": report_dict,
        "test_confusion_matrix": cm.tolist(),
        "feature_importances": [
            {"feature": n, "importance": float(g)} for n, g in importances
        ],
    }
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"Saved metrics       -> {METRICS_PATH}")


if __name__ == "__main__":
    main()
