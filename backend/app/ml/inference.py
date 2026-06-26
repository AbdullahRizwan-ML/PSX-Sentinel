"""
PSX Sentinel — Live ML inference for the Arbitrator (Phase 3 Session 3)

Loads the trained XGBoost price-direction model once at import time and
exposes a single function the Arbitrator (or any other agent) can call
to score the latest available trading day for a ticker.

Why a module-level singleton:
    The XGBoost model is small (~30 trees from Session 2's
    best_iteration of 27), but `XGBClassifier.load_model()` parses
    JSON from disk on every call. Caching it once at first use means
    each subsequent `predict_proba` is a microsecond-scale numpy op,
    matching the pattern already used elsewhere in this codebase for
    expensive-to-construct resources (LLMGateway, redis_client).

Why this lives outside app/ml/features.py:
    features.py is mostly batch / offline. inference.py is request-path
    code (called by the Arbitrator during a live analysis run). Keeping
    them in separate modules makes the boundary obvious — features.py
    has no model-loading side effects on import.

Why this doesn't go through LLMGateway:
    LLMGateway is for stochastic, network-bound LLM calls (tracking,
    failover, cost). This is a local deterministic numpy op on a
    pre-fit model file — exactly the kind of "pre-computed number"
    the Arbitrator already produces from technical/news/filing data
    before calling the LLM for narrative.

CONFIDENCE GATE — context for callers:
    The trained model scored 39.34% test accuracy on a 3-class problem
    (33.3% random baseline) — a real but very thin ~+6pp edge. It also
    structurally never predicts FLAT (0 of 1426 test predictions). This
    module exposes the raw probabilities AND a gate flag based on the
    caller's threshold; callers are expected to honour the gate rather
    than acting on every prediction. See docs/BUILD_LOG.md, Phase 3
    Session 2 entry, for full evaluation detail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb
from loguru import logger

from app.ml.features import FEATURE_COLUMNS, build_features_point_in_time

# Must match LABEL_TO_INT in scripts/train_ml_model.py exactly.
# DO NOT REORDER — XGBoost stores class indices, not names.
CLASS_NAMES = ["DOWN", "FLAT", "UP"]

# Path to the trained model artifact. Built by scripts/train_ml_model.py.
# Kept as a module-level constant so tests can monkey-patch.
MODEL_PATH = (
    Path(__file__).resolve().parent.parent.parent / "ml_data" / "model.json"
)

_model: xgb.XGBClassifier | None = None
_model_load_error: str | None = None


def _get_model() -> xgb.XGBClassifier | None:
    """
    Lazy-load the model on first use. Returns None (and remembers the
    error) if the model file is missing or fails to parse — callers
    must handle that path, since model absence is a survivable
    degradation (the rest of the Arbitrator pipeline still works).
    """
    global _model, _model_load_error
    if _model is not None:
        return _model
    if _model_load_error is not None:
        return None

    if not MODEL_PATH.exists():
        _model_load_error = f"model file not found at {MODEL_PATH}"
        logger.warning(
            f"ML inference disabled — {_model_load_error}. "
            f"Run scripts/train_ml_model.py to produce model.json."
        )
        return None

    try:
        m = xgb.XGBClassifier()
        m.load_model(str(MODEL_PATH))
        _model = m
        logger.info(f"Loaded XGBoost model from {MODEL_PATH}")
        return _model
    except Exception as e:
        _model_load_error = f"{type(e).__name__}: {e}"
        logger.error(f"Failed to load model from {MODEL_PATH}: {e}")
        return None


def predict_from_prices(
    prices: list[dict] | Any,
    confidence_threshold: float = 0.55,
) -> dict:
    """
    Score the latest trading day of `prices` with the trained model
    and decide whether the prediction clears the confidence gate.

    Parameters
    ----------
    prices : list of dicts (or anything convertible to a DataFrame)
        Each row must have at least 'date', 'close', and 'volume'.
        Caller typically passes context.recent_prices straight from
        the orchestrator.
    confidence_threshold : float
        max(predict_proba) must be strictly greater than this for
        `gate_passed` to be True. Default 0.55 matches the Session 2
        recommendation for the Arbitrator wiring.

    Returns
    -------
    dict with the shape (always returned, never raises):
        {
            "available": bool,
            "gate_passed": bool,
            "skip_reason": "model_unavailable" | "insufficient_history"
                           | "below_confidence_threshold" | None,
            "predicted_class": "UP" | "DOWN" | "FLAT" | None,
            "max_prob": float | None,
            "probabilities": {"DOWN": float, "FLAT": float, "UP": float}
                             | None,
            "as_of_date": str | None,
            "confidence_threshold": float,
        }

    `gate_passed` is True only when the model is loaded AND there was
    enough history AND max_prob > threshold. Any False case sets
    `skip_reason` so the caller can surface the *reason* the term
    contributed 0, not just the zero — same pattern the rest of the
    Arbitrator uses to distinguish "real signal" from "no signal".
    """
    result: dict[str, Any] = {
        "available": False,
        "gate_passed": False,
        "skip_reason": None,
        "predicted_class": None,
        "max_prob": None,
        "probabilities": None,
        "as_of_date": None,
        "confidence_threshold": confidence_threshold,
    }

    model = _get_model()
    if model is None:
        result["skip_reason"] = "model_unavailable"
        return result

    # Reuse the batch-path indicator logic via build_features_point_in_time.
    import pandas as pd

    try:
        prices_df = pd.DataFrame(prices) if not isinstance(
            prices, pd.DataFrame
        ) else prices
    except Exception:
        result["skip_reason"] = "insufficient_history"
        return result

    pit = build_features_point_in_time(prices_df)
    if pit is None:
        result["skip_reason"] = "insufficient_history"
        return result

    result["available"] = True
    result["as_of_date"] = pit["as_of_date"]

    feature_vector = np.array(
        [pit["features"][col] for col in FEATURE_COLUMNS],
        dtype=float,
    ).reshape(1, -1)

    proba = model.predict_proba(feature_vector)[0]
    if len(proba) != len(CLASS_NAMES):
        # Defensive: training used 3 classes; if the model file ever
        # diverges from that we want a loud, clean failure mode here
        # rather than a confusing IndexError downstream.
        logger.error(
            f"ML model returned {len(proba)} class probabilities, "
            f"expected {len(CLASS_NAMES)}"
        )
        result["available"] = False
        result["skip_reason"] = "model_unavailable"
        return result

    probabilities = {
        CLASS_NAMES[i]: float(proba[i]) for i in range(len(CLASS_NAMES))
    }
    max_idx = int(np.argmax(proba))
    max_prob = float(proba[max_idx])
    predicted_class = CLASS_NAMES[max_idx]

    result["probabilities"] = probabilities
    result["max_prob"] = max_prob
    result["predicted_class"] = predicted_class

    if max_prob > confidence_threshold:
        result["gate_passed"] = True
    else:
        result["skip_reason"] = "below_confidence_threshold"

    return result
