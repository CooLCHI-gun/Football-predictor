from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
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

PROXY_FEATURE_NAMES: list[str] = [
    "rd_pool_available_density",
    "rd_combination_available_density",
    "rd_combination_suspended_density",
    "rd_selection_count_total",
    "rd_unique_selection_count",
    "rd_market_depth_index",
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


def export_proxy_feature_monitor_report(
    *,
    feature_frame: pd.DataFrame,
    importance_df: pd.DataFrame,
    kickoff_series: pd.Series | None = None,
    output_json_path: Path = Path("artifacts/debug/proxy_feature_monitor.json"),
    output_csv_path: Path = Path("artifacts/debug/proxy_feature_monitor.csv"),
) -> tuple[Path, Path]:
    records: list[dict[str, object]] = []
    sorted_frame = _sort_feature_frame_for_drift(feature_frame=feature_frame, kickoff_series=kickoff_series)

    for feature_name in PROXY_FEATURE_NAMES:
        series = pd.to_numeric(sorted_frame.get(feature_name, pd.Series(dtype=float)), errors="coerce")
        importance_row = importance_df[importance_df["feature_name"] == feature_name]
        importance_value = float(importance_row["importance_value"].iloc[0]) if not importance_row.empty else 0.0
        importance_rank = int(importance_row["importance_rank"].iloc[0]) if not importance_row.empty else None

        drift = _compute_drift_metrics(series)
        records.append(
            {
                "feature_name": feature_name,
                "importance_value": importance_value,
                "importance_rank": importance_rank,
                "sample_count": int(len(series)),
                "missing_rate": float(series.isna().mean()) if len(series) else 1.0,
                "mean": _safe_float(series.mean()),
                "std": _safe_float(series.std(ddof=0)),
                **drift,
            }
        )

    report_df = pd.DataFrame(records).sort_values("importance_value", ascending=False).reset_index(drop=True)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "proxy_feature_count": int(len(report_df)),
        "nonzero_importance_count": int((report_df["importance_value"] > 0).sum()) if not report_df.empty else 0,
        "top_importance_feature": (
            str(report_df.iloc[0]["feature_name"]) if not report_df.empty else None
        ),
        "max_drift_feature": (
            str(report_df.sort_values("drift_ratio", ascending=False).iloc[0]["feature_name"])
            if not report_df.empty
            else None
        ),
    }

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    output_csv_path.write_text(report_df.to_csv(index=False), encoding="utf-8")
    output_json_path.write_text(
        json_dumps(
            {
                "summary": summary,
                "features": report_df.to_dict(orient="records"),
            }
        ),
        encoding="utf-8",
    )
    return output_json_path, output_csv_path


def evaluate_proxy_availability_alerts(
    *,
    proxy_monitor_csv_path: Path,
    history_path: Path = Path("artifacts/debug/proxy_feature_alert_history.json"),
    missing_rate_threshold: float = 0.9,
    consecutive_runs: int = 3,
    run_tag: str | None = None,
) -> dict[str, Any]:
    if not proxy_monitor_csv_path.exists():
        return {
            "enabled": False,
            "reason": f"proxy monitor file missing: {proxy_monitor_csv_path}",
            "threshold": missing_rate_threshold,
            "consecutive_runs": consecutive_runs,
            "alerts": [],
            "per_feature": [],
        }

    report_df = pd.read_csv(proxy_monitor_csv_path)
    run_id = run_tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    history = _load_proxy_alert_history(history_path)
    runs = list(history.get("runs", [])) if isinstance(history, dict) else []

    current_map: dict[str, bool] = {}
    for feature_name in PROXY_FEATURE_NAMES:
        row = report_df[report_df["feature_name"] == feature_name]
        if row.empty:
            current_map[feature_name] = True
            continue
        missing_rate = float(pd.to_numeric(row["missing_rate"], errors="coerce").iloc[0])
        if np.isnan(missing_rate):
            missing_rate = 1.0
        current_map[feature_name] = bool(missing_rate > missing_rate_threshold)

    runs.append(
        {
            "run_id": run_id,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "missing_rate_over_threshold": current_map,
        }
    )
    if len(runs) > 300:
        runs = runs[-300:]

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json_dumps(
            {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runs": runs,
            }
        ),
        encoding="utf-8",
    )

    per_feature: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for feature_name in PROXY_FEATURE_NAMES:
        streak = _compute_feature_streak(runs=runs, feature_name=feature_name)
        row = report_df[report_df["feature_name"] == feature_name]
        missing_rate = float(pd.to_numeric(row["missing_rate"], errors="coerce").iloc[0]) if not row.empty else 1.0
        if np.isnan(missing_rate):
            missing_rate = 1.0

        item = {
            "feature_name": feature_name,
            "missing_rate": missing_rate,
            "over_threshold": bool(current_map.get(feature_name, False)),
            "consecutive_over_threshold": streak,
            "threshold": missing_rate_threshold,
        }
        per_feature.append(item)
        if streak >= consecutive_runs:
            alerts.append(
                {
                    "feature_name": feature_name,
                    "severity": "warning",
                    "message": (
                        f"missing_rate {missing_rate:.3f} > {missing_rate_threshold:.3f} "
                        f"for {streak} consecutive runs"
                    ),
                    "consecutive_over_threshold": streak,
                }
            )

    return {
        "enabled": True,
        "run_id": run_id,
        "history_path": str(history_path),
        "threshold": missing_rate_threshold,
        "consecutive_runs": consecutive_runs,
        "alert_count": len(alerts),
        "alerts": alerts,
        "per_feature": per_feature,
    }


def _sort_feature_frame_for_drift(*, feature_frame: pd.DataFrame, kickoff_series: pd.Series | None) -> pd.DataFrame:
    frame = feature_frame.copy()
    if kickoff_series is None or len(kickoff_series) != len(frame):
        return frame.reset_index(drop=True)

    kickoff = pd.to_datetime(kickoff_series, utc=True, errors="coerce")
    temp = frame.copy()
    temp["__kickoff_time_utc__"] = kickoff
    temp = temp.sort_values("__kickoff_time_utc__", kind="mergesort").drop(columns=["__kickoff_time_utc__"])
    return temp.reset_index(drop=True)


def _compute_drift_metrics(series: pd.Series) -> dict[str, float | None]:
    if len(series) == 0:
        return {
            "first_half_mean": None,
            "second_half_mean": None,
            "first_half_missing_rate": None,
            "second_half_missing_rate": None,
            "drift_abs": None,
            "drift_ratio": None,
            "drift_cohen_d": None,
        }

    midpoint = max(1, len(series) // 2)
    first_half = series.iloc[:midpoint]
    second_half = series.iloc[midpoint:]
    if second_half.empty:
        second_half = first_half

    first_mean = _safe_float(first_half.mean())
    second_mean = _safe_float(second_half.mean())
    first_missing = float(first_half.isna().mean())
    second_missing = float(second_half.isna().mean())

    if first_mean is None or second_mean is None:
        drift_abs = None
        drift_ratio = None
    else:
        drift_abs = abs(second_mean - first_mean)
        drift_ratio = drift_abs / (abs(first_mean) + 1e-8)

    first_std = _safe_float(first_half.std(ddof=0))
    second_std = _safe_float(second_half.std(ddof=0))
    pooled = None
    if first_std is not None and second_std is not None:
        pooled = float(np.sqrt((first_std**2 + second_std**2) / 2.0))

    if pooled is None or pooled <= 0 or first_mean is None or second_mean is None:
        cohen_d = None
    else:
        cohen_d = float((second_mean - first_mean) / pooled)

    return {
        "first_half_mean": first_mean,
        "second_half_mean": second_mean,
        "first_half_missing_rate": first_missing,
        "second_half_missing_rate": second_missing,
        "drift_abs": drift_abs,
        "drift_ratio": drift_ratio,
        "drift_cohen_d": cohen_d,
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(parsed):
        return None
    return parsed


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def _load_proxy_alert_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"runs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"runs": []}
    if not isinstance(payload, dict):
        return {"runs": []}
    if not isinstance(payload.get("runs"), list):
        payload["runs"] = []
    return payload


def _compute_feature_streak(*, runs: list[dict[str, Any]], feature_name: str) -> int:
    streak = 0
    for item in reversed(runs):
        flags = item.get("missing_rate_over_threshold")
        if not isinstance(flags, dict):
            break
        if bool(flags.get(feature_name, False)):
            streak += 1
            continue
        break
    return streak


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
