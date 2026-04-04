from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance


GROUP_PREFIX_RULES: list[tuple[str, str]] = [
    ("h2h_", "h2h"),
    ("recent", "recent_form"),
    ("rest_days", "recent_form"),
    ("hk_", "hk_vs_consensus"),
    ("consensus_", "hk_vs_consensus"),
    ("implied_prob", "market"),
    ("odds_", "market"),
    ("handicap_", "market"),
    ("elo_", "team_strength"),
    ("form_", "team_strength"),
    ("goals_", "team_strength"),
    ("goal_diff", "team_strength"),
    ("history_", "team_strength"),
]


def infer_feature_group(feature_name: str) -> str:
    normalized = feature_name.strip().lower()
    for prefix, group in GROUP_PREFIX_RULES:
        if normalized.startswith(prefix):
            return group
    if "h2h" in normalized:
        return "h2h"
    if "recent" in normalized or "rest" in normalized:
        return "recent_form"
    if "consensus" in normalized or normalized.startswith("hk"):
        return "hk_vs_consensus"
    if "odds" in normalized or "implied" in normalized or "line" in normalized:
        return "market"
    if "elo" in normalized or "form" in normalized or "goal" in normalized:
        return "team_strength"
    return "other"


def export_feature_importance_debug(
    *,
    bundle: Any,
    feature_frame: pd.DataFrame,
    y: np.ndarray,
    output_path: Path = Path("artifacts/debug/feature_importance.csv"),
    group_output_path: Path = Path("artifacts/debug/feature_group_importance.csv"),
    max_fallback_rows: int = 1200,
    random_state: int = 42,
) -> tuple[Path, Path]:
    values = _extract_importance_values(bundle=bundle, feature_frame=feature_frame, y=y, max_rows=max_fallback_rows, random_state=random_state)
    importance_df = pd.DataFrame(
        {
            "feature_name": feature_frame.columns,
            "importance_value": values,
        }
    )
    importance_df["importance_value"] = pd.to_numeric(importance_df["importance_value"], errors="coerce").fillna(0.0)
    importance_df["feature_group"] = importance_df["feature_name"].map(infer_feature_group)
    importance_df = importance_df.sort_values("importance_value", ascending=False).reset_index(drop=True)
    importance_df["importance_rank"] = np.arange(1, len(importance_df) + 1)
    importance_df = importance_df[["feature_name", "importance_value", "importance_rank", "feature_group"]]

    group_df = (
        importance_df.groupby("feature_group", dropna=False)
        .agg(
            importance_sum=("importance_value", "sum"),
            importance_mean=("importance_value", "mean"),
            feature_count=("feature_name", "count"),
        )
        .reset_index()
    )
    total = float(group_df["importance_sum"].sum())
    group_df["importance_share"] = group_df["importance_sum"] / total if total > 0 else 0.0
    group_df = group_df.sort_values("importance_sum", ascending=False).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    group_output_path.parent.mkdir(parents=True, exist_ok=True)
    importance_df.to_csv(output_path, index=False)
    group_df.to_csv(group_output_path, index=False)
    return output_path, group_output_path


def _extract_importance_values(
    *,
    bundle: Any,
    feature_frame: pd.DataFrame,
    y: np.ndarray,
    max_rows: int,
    random_state: int,
) -> np.ndarray:
    native = _extract_native_importance(bundle=bundle, feature_count=feature_frame.shape[1])
    if native is not None:
        return native

    estimator = getattr(bundle, "estimator", None)
    if estimator is None or feature_frame.empty or len(y) == 0:
        return np.zeros(feature_frame.shape[1], dtype=float)

    sample_size = min(len(feature_frame), max_rows)
    if sample_size <= 0:
        return np.zeros(feature_frame.shape[1], dtype=float)
    sample_index = feature_frame.sample(n=sample_size, random_state=random_state).index
    X_sample = feature_frame.loc[sample_index]
    y_sample = np.asarray(y)[sample_index.to_numpy()]

    scoring = "neg_log_loss" if getattr(bundle, "approach", "direct_cover") == "direct_cover" else "neg_mean_squared_error"
    try:
        result = permutation_importance(
            estimator,
            X_sample,
            y_sample,
            scoring=scoring,
            n_repeats=3,
            random_state=random_state,
        )
        values = np.asarray(result.importances_mean, dtype=float)
        return np.nan_to_num(np.abs(values), nan=0.0)
    except Exception:
        return np.zeros(feature_frame.shape[1], dtype=float)


def _extract_native_importance(bundle: Any, feature_count: int) -> np.ndarray | None:
    estimator = getattr(bundle, "estimator", None)
    if estimator is None:
        return None

    direct = _read_importance_from_estimator(estimator)
    if direct is not None and len(direct) == feature_count:
        return direct

    calibrated_estimators = getattr(estimator, "calibrated_classifiers_", None)
    if calibrated_estimators:
        values: list[np.ndarray] = []
        for item in calibrated_estimators:
            base_estimator = getattr(item, "estimator", None)
            candidate = _read_importance_from_estimator(base_estimator)
            if candidate is not None and len(candidate) == feature_count:
                values.append(candidate)
        if values:
            return np.mean(np.vstack(values), axis=0)

    return None


def _read_importance_from_estimator(estimator: Any) -> np.ndarray | None:
    if estimator is None:
        return None

    for attr in ("feature_importances_", "coef_"):
        if hasattr(estimator, attr):
            raw = np.asarray(getattr(estimator, attr), dtype=float)
            values = np.abs(raw).ravel()
            return np.nan_to_num(values, nan=0.0)

    named_steps = getattr(estimator, "named_steps", None)
    if isinstance(named_steps, dict):
        for step_name in ("classifier", "regressor"):
            if step_name in named_steps:
                return _read_importance_from_estimator(named_steps[step_name])
    return None
