from __future__ import annotations

import json
import logging
import math
import pickle
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from xgboost import XGBClassifier, XGBRegressor
except ImportError:  # pragma: no cover - optional dependency
    XGBClassifier = None
    XGBRegressor = None

try:
    from lightgbm import LGBMClassifier, LGBMRegressor
except ImportError:  # pragma: no cover - optional dependency
    LGBMClassifier = None
    LGBMRegressor = None

from src.strategy.settlement import settle_handicap_bet


LOGGER = logging.getLogger(__name__)


BASE_FEATURE_COLUMNS = [
    "home_form_points_last5",
    "home_form_points_last10",
    "away_form_points_last5",
    "away_form_points_last10",
    "home_recent_home_form_last5",
    "away_recent_away_form_last5",
    "home_goals_scored_last5",
    "home_goals_conceded_last5",
    "home_goal_diff_last5",
    "away_goals_scored_last5",
    "away_goals_conceded_last5",
    "away_goal_diff_last5",
    "rest_days_home",
    "rest_days_away",
    "rest_days_diff",
    "recent5_rest_days_diff",
    "recent5_hdc_cover_rate_home",
    "recent10_hdc_cover_rate_home",
    "recent5_hdc_cover_rate_away",
    "recent10_hdc_cover_rate_away",
    "recent5_goal_diff_mean_home",
    "recent10_goal_diff_mean_home",
    "recent5_goal_diff_mean_away",
    "recent10_goal_diff_mean_away",
    "recent5_xg_diff_mean_home",
    "recent5_xg_diff_mean_away",
    "recent10_xg_diff_mean_home",
    "recent10_xg_diff_mean_away",
    "recent_hdc_cover_ewm_alpha_0p3_home",
    "recent_hdc_cover_ewm_alpha_0p3_away",
    "recent5_hdc_cover_advantage",
    "recent10_hdc_cover_advantage",
    "h2h_last5_hdc_cover_rate",
    "h2h_last10_hdc_cover_rate",
    "h2h_home_last5_hdc_cover_rate",
    "h2h_last5_hdc_cover_mean",
    "h2h_last10_hdc_cover_mean",
    "h2h_last5_goal_diff_mean",
    "h2h_last5_xg_diff_mean",
    "h2h_sample_size_last5",
    "h2h_sample_size_last10",
    "elo_home_pre",
    "elo_away_pre",
    "elo_diff_pre",
    "history_home_matches_count",
    "history_away_matches_count",
    "missing_home_history_flag",
    "missing_away_history_flag",
    "missing_home_ft_goals_flag",
    "missing_away_ft_goals_flag",
]

MARKET_FEATURE_COLUMNS = [
    "handicap_open_line",
    "handicap_close_line",
    "handicap_line_movement",
    "missing_handicap_line_flag",
    "odds_home_open",
    "odds_away_open",
    "odds_home_close",
    "odds_away_close",
    "implied_prob_home_open",
    "implied_prob_away_open",
    "implied_prob_home_close",
    "implied_prob_away_close",
    "hk_line",
    "consensus_line",
    "hk_line_minus_consensus_line",
    "hk_implied_prob",
    "hk_implied_prob_side",
    "consensus_implied_prob",
    "consensus_implied_prob_side",
    "hk_minus_consensus_prob",
    "hk_off_market_flag",
    "hk_off_market_direction",
    "hk_off_market_agree_with_model_flag",
    "rd_pool_available_density",
    "rd_combination_available_density",
    "rd_combination_suspended_density",
    "rd_selection_count_total",
    "rd_unique_selection_count",
    "rd_market_depth_index",
    "missing_odds_flag",
]

@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    mean_observed: float


@dataclass(frozen=True)
class TrainingReport:
    model_name: str
    approach: str
    include_market_features: bool
    sample_size: int
    positive_rate: float | None
    brier_score: float | None
    log_loss: float | None
    calibration_method: str
    calibration_bins: list[CalibrationBin]
    note: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["calibration_bins"] = [asdict(item) for item in self.calibration_bins]
        return payload


@dataclass
class ModelBundle:
    model_name: str
    approach: str
    include_market_features: bool
    feature_columns: list[str]
    estimator: Any | None
    calibration_method: str
    fallback_note: str | None
    residual_std: float | None = None


def train_model_bundle(
    df: pd.DataFrame,
    model_name: str,
    approach: str,
    include_market_features: bool,
    previous_bundle: ModelBundle | None = None,
) -> tuple[ModelBundle, TrainingReport]:
    training_df = prepare_training_frame(df=df, approach=approach)
    feature_columns = get_feature_columns(include_market_features=include_market_features)
    feature_frame = build_feature_frame(training_df, feature_columns)

    if approach == "direct_cover":
        target = training_df["target_direct_cover"].astype(int).to_numpy()
        unique_classes = np.unique(target)
        if len(unique_classes) < 2:
            can_reuse_previous = (
                previous_bundle is not None
                and previous_bundle.approach == approach
                and previous_bundle.include_market_features == include_market_features
            )
            if can_reuse_previous:
                assert previous_bundle is not None
                bundle = previous_bundle
                probabilities = predict_home_cover_probability(bundle=bundle, df=training_df)
                fallback_note = (
                    "Direct-cover fold retrain skipped: training target has <2 classes; "
                    "reused previous fold model bundle."
                )
                LOGGER.warning(
                    "Skip direct_cover retrain (single-class target); reusing previous bundle. "
                    "rows=%s unique_classes=%s",
                    len(training_df),
                    unique_classes.tolist(),
                )
            else:
                bundle, probabilities = _train_direct_cover_model(
                    model_name="rule_based",
                    X=feature_frame,
                    y=target,
                    include_market_features=include_market_features,
                )
                fallback_note = (
                    "Direct-cover fold retrain skipped: training target has <2 classes and no previous fold model; "
                    "fallback to rule_based baseline for this fold."
                )
                LOGGER.warning(
                    "Skip direct_cover retrain (single-class target) with no previous bundle; "
                    "fallback to rule_based baseline. rows=%s unique_classes=%s",
                    len(training_df),
                    unique_classes.tolist(),
                )
        else:
            bundle, probabilities = _train_direct_cover_model(
                model_name=model_name,
                X=feature_frame,
                y=target,
                include_market_features=include_market_features,
            )
            fallback_note = bundle.fallback_note

        report = TrainingReport(
            model_name=bundle.model_name,
            approach=approach,
            include_market_features=include_market_features,
            sample_size=len(training_df),
            positive_rate=float(np.mean(target)) if len(target) else None,
            brier_score=float(brier_score_loss(target, probabilities)) if len(target) else None,
            log_loss=float(log_loss(target, probabilities, labels=[0, 1])) if len(target) else None,
            calibration_method=bundle.calibration_method,
            calibration_bins=build_calibration_bins(target, probabilities),
            note=fallback_note,
        )
        bundle.feature_columns = feature_columns
        return bundle, report

    if approach == "goal_diff":
        target = training_df["target_goal_diff"].astype(float).to_numpy()
        bundle = _train_goal_diff_model(
            model_name=model_name,
            X=feature_frame,
            y=target,
            include_market_features=include_market_features,
        )
        probabilities = predict_home_cover_probability(bundle=bundle, df=training_df)
        direct_labels = training_df["target_direct_cover"].astype(int).to_numpy()
        report = TrainingReport(
            model_name=bundle.model_name,
            approach=approach,
            include_market_features=include_market_features,
            sample_size=len(training_df),
            positive_rate=float(np.mean(direct_labels)) if len(direct_labels) else None,
            brier_score=float(brier_score_loss(direct_labels, probabilities)) if len(direct_labels) else None,
            log_loss=float(log_loss(direct_labels, probabilities, labels=[0, 1])) if len(direct_labels) else None,
            calibration_method=bundle.calibration_method,
            calibration_bins=build_calibration_bins(direct_labels, probabilities),
            note=bundle.fallback_note,
        )
        bundle.feature_columns = feature_columns
        return bundle, report

    raise ValueError(f"Unsupported approach: {approach}")


def generate_prediction_frame(bundle: ModelBundle, df: pd.DataFrame) -> pd.DataFrame:
    probabilities = predict_home_cover_probability(bundle=bundle, df=df)
    prediction_df = df.copy()
    prediction_df["home_cover_probability"] = probabilities
    prediction_df["away_cover_probability"] = 1.0 - prediction_df["home_cover_probability"]
    prediction_df["predicted_side"] = np.where(
        prediction_df["home_cover_probability"] >= 0.5,
        "home",
        "away",
    )
    prediction_df["model_probability"] = np.where(
        prediction_df["predicted_side"] == "home",
        prediction_df["home_cover_probability"],
        prediction_df["away_cover_probability"],
    )
    prediction_df["confidence_score"] = (prediction_df["model_probability"] - 0.5).abs() * 2.0
    prediction_df["model_name"] = bundle.model_name
    prediction_df["model_approach"] = bundle.approach
    prediction_df["market_feature_variant"] = np.where(bundle.include_market_features, "market", "base")

    if bundle.approach == "goal_diff":
        prediction_df["predicted_goal_diff"] = predict_goal_diff(bundle=bundle, df=df)

    return prediction_df


def predict_home_cover_probability(bundle: ModelBundle, df: pd.DataFrame) -> np.ndarray:
    feature_frame = build_feature_frame(df=df, feature_columns=bundle.feature_columns)

    if bundle.model_name == "rule_based":
        return _rule_based_probability(df=feature_frame, include_market_features=bundle.include_market_features)

    if bundle.approach == "goal_diff":
        predicted_goal_diff = predict_goal_diff(bundle=bundle, df=df)
        return np.array(
            [
                _goal_diff_to_cover_probability(
                    mean_goal_diff=float(goal_diff),
                    handicap_line=float(line) if not pd.isna(line) else 0.0,
                    residual_std=bundle.residual_std or 1.0,
                )
                for goal_diff, line in zip(predicted_goal_diff, df["handicap_close_line"].fillna(0.0), strict=False)
            ]
        )

    if bundle.estimator is None:
        raise ValueError("Model estimator is not available for probability prediction.")
    probabilities = bundle.estimator.predict_proba(feature_frame)[:, 1]
    return np.asarray(probabilities, dtype=float)


def predict_goal_diff(bundle: ModelBundle, df: pd.DataFrame) -> np.ndarray:
    if bundle.model_name == "rule_based":
        feature_frame = build_feature_frame(df=df, feature_columns=bundle.feature_columns)
        return _rule_based_goal_diff(feature_frame)
    feature_frame = build_feature_frame(df=df, feature_columns=bundle.feature_columns)
    if bundle.estimator is None:
        raise ValueError("Model estimator is not available for goal-difference prediction.")
    return np.asarray(bundle.estimator.predict(feature_frame), dtype=float)


def save_model_bundle(bundle: ModelBundle, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as file_handle:
        pickle.dump(bundle, file_handle)


def load_model_bundle(model_path: Path) -> ModelBundle:
    with model_path.open("rb") as file_handle:
        return pickle.load(file_handle)


def save_training_report(report: TrainingReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def prepare_training_frame(df: pd.DataFrame, approach: str) -> pd.DataFrame:
    training_df = normalize_training_columns(df.copy())
    required = {"ft_home_goals", "ft_away_goals", "handicap_close_line", "odds_home_close"}
    missing = [column for column in required if column not in training_df.columns]
    if missing:
        raise ValueError(f"Feature dataset missing required training columns: {', '.join(missing)}")

    if "target_handicap_side" not in training_df.columns:
        training_df["target_handicap_side"] = "home"

    training_df["target_direct_cover"] = training_df.apply(_direct_cover_target, axis=1)
    training_df["target_goal_diff"] = training_df["ft_home_goals"].astype(float) - training_df["ft_away_goals"].astype(float)

    if approach == "direct_cover":
        return training_df.dropna(subset=["target_direct_cover"])
    if approach == "goal_diff":
        return training_df.dropna(subset=["target_goal_diff"])
    raise ValueError(f"Unsupported approach: {approach}")


def get_feature_columns(include_market_features: bool) -> list[str]:
    if include_market_features:
        return BASE_FEATURE_COLUMNS + MARKET_FEATURE_COLUMNS
    return BASE_FEATURE_COLUMNS.copy()


def build_feature_frame(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    feature_frame = pd.DataFrame(index=df.index)
    for column in feature_columns:
        if column in df.columns:
            feature_frame[column] = pd.to_numeric(df[column], errors="coerce")
        else:
            feature_frame[column] = np.nan
    # rest_days_* are computed from previous-match kickoff times; the very first match
    # per team yields NaN (no prior match), and walk-forward inner CV splits can also
    # be all-NaN for these columns.  Fill with 0.0 as "rest unknown / first appearance"
    # placeholder so sklearn's median imputer never receives an all-NaN column.
    for col in ["rest_days_home", "rest_days_away"]:
        if col in feature_frame.columns:
            feature_frame[col] = feature_frame[col].fillna(0.0)
    return feature_frame


def build_calibration_bins(y_true: np.ndarray, y_prob: np.ndarray, bins: int = 5) -> list[CalibrationBin]:
    if len(y_true) == 0:
        return []

    edges = np.linspace(0.0, 1.0, bins + 1)
    result: list[CalibrationBin] = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=False):
        if upper == 1.0:
            mask = (y_prob >= lower) & (y_prob <= upper)
        else:
            mask = (y_prob >= lower) & (y_prob < upper)
        if not np.any(mask):
            continue
        result.append(
            CalibrationBin(
                lower=float(lower),
                upper=float(upper),
                count=int(mask.sum()),
                mean_predicted=float(np.mean(y_prob[mask])),
                mean_observed=float(np.mean(y_true[mask])),
            )
        )
    return result


def _train_direct_cover_model(
    model_name: str,
    X: pd.DataFrame,
    y: np.ndarray,
    include_market_features: bool,
) -> tuple[ModelBundle, np.ndarray]:
    if len(np.unique(y)) < 2:
        probabilities = np.full(shape=len(X), fill_value=float(np.mean(y)) if len(y) else 0.5, dtype=float)
        return (
            ModelBundle(
                model_name="rule_based",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=None,
                calibration_method="heuristic",
                fallback_note="Single-class target detected; fallback to rule_based baseline to avoid training failure.",
            ),
            probabilities,
        )

    if model_name == "rule_based":
        probabilities = _rule_based_probability(df=X, include_market_features=include_market_features)
        return (
            ModelBundle(
                model_name="rule_based",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=None,
                calibration_method="heuristic",
                fallback_note="Rule-based baseline uses deterministic heuristic scoring, not statistical fitting.",
            ),
            probabilities,
        )

    if model_name == "logistic_regression":
        estimator, calibration_method = _build_logistic_estimator(y=y)
        estimator.fit(X, y)
        probabilities = estimator.predict_proba(X)[:, 1]
        return (
            ModelBundle(
                model_name="logistic_regression",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=estimator,
                calibration_method=calibration_method,
                fallback_note=None if calibration_method != "none" else "Calibration fallback to raw logistic probabilities due to limited class counts.",
            ),
            np.asarray(probabilities, dtype=float),
        )

    if model_name == "gradient_boosting":
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("classifier", HistGradientBoostingClassifier(random_state=42)),
            ]
        )
        estimator.fit(X, y)
        probabilities = estimator.predict_proba(X)[:, 1]
        return (
            ModelBundle(
                model_name="gradient_boosting",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=estimator,
                calibration_method="none",
                fallback_note="Using scikit-learn HistGradientBoostingClassifier as the Phase 3 tree-based baseline.",
            ),
            np.asarray(probabilities, dtype=float),
        )

    if model_name == "xgboost":
        if XGBClassifier is None:
            raise ValueError("xgboost is not installed. Install package 'xgboost' to use model_name=xgboost.")
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "classifier",
                    XGBClassifier(
                        random_state=42,
                        n_estimators=300,
                        max_depth=4,
                        learning_rate=0.05,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        objective="binary:logistic",
                        eval_metric="logloss",
                    ),
                ),
            ]
        )
        estimator.fit(X, y)
        probabilities = estimator.predict_proba(X)[:, 1]
        return (
            ModelBundle(
                model_name="xgboost",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=estimator,
                calibration_method="none",
                fallback_note="Using XGBoost classifier as high-capacity tree model for direct_cover.",
            ),
            np.asarray(probabilities, dtype=float),
        )

    if model_name == "lightgbm":
        if LGBMClassifier is None:
            raise ValueError("lightgbm is not installed. Install package 'lightgbm' to use model_name=lightgbm.")
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "classifier",
                    LGBMClassifier(
                        random_state=42,
                        n_estimators=300,
                        max_depth=-1,
                        learning_rate=0.05,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        verbose=-1,
                    ),
                ),
            ]
        )
        estimator.fit(X, y)
        probabilities = estimator.predict_proba(X)[:, 1]
        return (
            ModelBundle(
                model_name="lightgbm",
                approach="direct_cover",
                include_market_features=include_market_features,
                feature_columns=[],
                estimator=estimator,
                calibration_method="none",
                fallback_note="Using LightGBM classifier as high-capacity tree model for direct_cover.",
            ),
            np.asarray(probabilities, dtype=float),
        )

    raise ValueError(f"Unsupported model_name: {model_name}")


def _train_goal_diff_model(
    model_name: str,
    X: pd.DataFrame,
    y: np.ndarray,
    include_market_features: bool,
) -> ModelBundle:
    if model_name == "logistic_regression":
        raise ValueError(
            "logistic_regression supports direct_cover only. "
            "Use gradient_boosting, xgboost, lightgbm or rule_based for goal_diff."
        )

    if model_name == "rule_based":
        predicted = _rule_based_goal_diff(X)
        residual_std = float(np.std(y - predicted)) or 1.0
        return ModelBundle(
            model_name="rule_based",
            approach="goal_diff",
            include_market_features=include_market_features,
            feature_columns=[],
            estimator=None,
            calibration_method="normal_residual_mapping",
            fallback_note="Rule-based goal-difference baseline maps heuristic score through empirical residual variance.",
            residual_std=max(residual_std, 0.25),
        )

    if model_name == "gradient_boosting":
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("regressor", HistGradientBoostingRegressor(random_state=42)),
            ]
        )
        estimator.fit(X, y)
        predicted = estimator.predict(X)
        residual_std = float(np.std(y - predicted)) or 1.0
        return ModelBundle(
            model_name="gradient_boosting",
            approach="goal_diff",
            include_market_features=include_market_features,
            feature_columns=[],
            estimator=estimator,
            calibration_method="normal_residual_mapping",
            fallback_note="Using scikit-learn HistGradientBoostingRegressor as the Phase 3 expected goal-difference baseline.",
            residual_std=max(residual_std, 0.25),
        )

    if model_name == "xgboost":
        if XGBRegressor is None:
            raise ValueError("xgboost is not installed. Install package 'xgboost' to use model_name=xgboost.")
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "regressor",
                    XGBRegressor(
                        random_state=42,
                        n_estimators=300,
                        max_depth=4,
                        learning_rate=0.05,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        objective="reg:squarederror",
                    ),
                ),
            ]
        )
        estimator.fit(X, y)
        predicted = estimator.predict(X)
        residual_std = float(np.std(y - predicted)) or 1.0
        return ModelBundle(
            model_name="xgboost",
            approach="goal_diff",
            include_market_features=include_market_features,
            feature_columns=[],
            estimator=estimator,
            calibration_method="normal_residual_mapping",
            fallback_note="Using XGBoost regressor for expected goal-difference modeling.",
            residual_std=max(residual_std, 0.25),
        )

    if model_name == "lightgbm":
        if LGBMRegressor is None:
            raise ValueError("lightgbm is not installed. Install package 'lightgbm' to use model_name=lightgbm.")
        estimator = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "regressor",
                    LGBMRegressor(
                        random_state=42,
                        n_estimators=300,
                        max_depth=-1,
                        learning_rate=0.05,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        verbose=-1,
                    ),
                ),
            ]
        )
        estimator.fit(X, y)
        predicted = estimator.predict(X)
        residual_std = float(np.std(y - predicted)) or 1.0
        return ModelBundle(
            model_name="lightgbm",
            approach="goal_diff",
            include_market_features=include_market_features,
            feature_columns=[],
            estimator=estimator,
            calibration_method="normal_residual_mapping",
            fallback_note="Using LightGBM regressor for expected goal-difference modeling.",
            residual_std=max(residual_std, 0.25),
        )

    raise ValueError(f"Unsupported model_name for goal_diff: {model_name}")


def _build_logistic_estimator(y: np.ndarray) -> tuple[Pipeline | CalibratedClassifierCV, str]:
    base_pipeline = Pipeline(
        steps=[
            (
                "preprocess",
                ColumnTransformer(
                    transformers=[
                        (
                            "numeric",
                            Pipeline(
                                steps=[
                                    ("imputer", SimpleImputer(strategy="median")),
                                    ("scaler", StandardScaler()),
                                ]
                            ),
                            slice(0, None),
                        )
                    ],
                    remainder="drop",
                ),
            ),
            ("classifier", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )

    class_counts = np.bincount(y)
    if len(class_counts) >= 2 and int(class_counts.min()) >= 3 and len(y) >= 12:
        return CalibratedClassifierCV(estimator=base_pipeline, method="sigmoid", cv=3), "sigmoid"
    return base_pipeline, "none"


def _direct_cover_target(row: pd.Series) -> float | None:
    line = row.get("handicap_close_line")
    odds = row.get("odds_home_close")
    if pd.isna(line) or pd.isna(odds):
        return None
    target_side = str(row.get("target_handicap_side", "home")).strip().lower()
    if target_side not in {"home", "away"}:
        target_side = "home"
    result = settle_handicap_bet(
        home_goals=int(row["ft_home_goals"]),
        away_goals=int(row["ft_away_goals"]),
        handicap_side=target_side,
        handicap_line=float(line),
        odds=float(odds),
        stake=1.0,
    )
    return 1.0 if result.pnl > 0 else 0.0


def normalize_training_columns(df: pd.DataFrame) -> pd.DataFrame:
    alias_groups = {
        "ft_home_goals": ["home_goals", "full_time_home_goals"],
        "ft_away_goals": ["away_goals", "full_time_away_goals"],
        "handicap_close_line": ["handicap_closing_line", "closing_handicap_line", "ah_close_line"],
        "target_handicap_side": ["handicap_side", "bet_side", "label_side"],
    }

    for target_column, aliases in alias_groups.items():
        if target_column in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                df[target_column] = df[alias]
                break

    return df


def _rule_based_probability(df: pd.DataFrame, include_market_features: bool) -> np.ndarray:
    elo_term = df["elo_diff_pre"].fillna(0.0) / 120.0
    form_term = (df["home_form_points_last5"].fillna(0.0) - df["away_form_points_last5"].fillna(0.0)) / 8.0
    goal_term = (df["home_goal_diff_last5"].fillna(0.0) - df["away_goal_diff_last5"].fillna(0.0)) / 6.0
    rest_term = (df["rest_days_home"].fillna(0.0) - df["rest_days_away"].fillna(0.0)) / 14.0

    total_score = 0.45 * elo_term + 0.25 * form_term + 0.20 * goal_term + 0.10 * rest_term
    if include_market_features:
        market_term = (
            df.get("implied_prob_home_close", pd.Series(0.5, index=df.index)).fillna(0.5)
            - df.get("implied_prob_away_close", pd.Series(0.5, index=df.index)).fillna(0.5)
        )
        line_term = -df.get("handicap_close_line", pd.Series(0.0, index=df.index)).fillna(0.0) / 2.0
        total_score = total_score + 0.15 * market_term + 0.10 * line_term

    clipped = np.clip(total_score, -6.0, 6.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _rule_based_goal_diff(df: pd.DataFrame) -> np.ndarray:
    return (
        df["elo_diff_pre"].fillna(0.0) / 100.0
        + (df["home_goal_diff_last5"].fillna(0.0) - df["away_goal_diff_last5"].fillna(0.0)) / 5.0
        + (df["home_form_points_last5"].fillna(0.0) - df["away_form_points_last5"].fillna(0.0)) / 10.0
    ).to_numpy(dtype=float)


def _goal_diff_to_cover_probability(mean_goal_diff: float, handicap_line: float, residual_std: float) -> float:
    line_components = _line_components(handicap_line)
    probabilities = [1.0 - _normal_cdf((-line) - mean_goal_diff, residual_std) for line in line_components]
    return float(np.clip(np.mean(probabilities), 0.0, 1.0))


def _line_components(handicap_line: float) -> list[float]:
    quarter_steps = round(handicap_line * 4)
    remainder = abs(quarter_steps) % 4
    if remainder in {1, 3}:
        return [handicap_line - 0.25, handicap_line + 0.25]
    return [handicap_line]


def _normal_cdf(value: float, std_dev: float) -> float:
    denominator = max(std_dev, 1e-6) * math.sqrt(2.0)
    return 0.5 * (1.0 + math.erf(value / denominator))
