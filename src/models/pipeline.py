from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.debug.feature_importance import export_feature_importance_debug
from src.models.baselines import (
    build_feature_frame,
    generate_prediction_frame,
    get_feature_columns,
    load_model_bundle,
    prepare_training_frame,
    save_model_bundle,
    save_training_report,
    train_model_bundle,
)


LOGGER = logging.getLogger(__name__)


def train_model_command(
    input_path: Path,
    model_name: str,
    approach: str,
    include_market_features: bool,
    model_output_path: Path,
    report_output_path: Path,
) -> str:
    """Train configured model and persist model bundle plus training report."""
    LOGGER.info("Model train started: input=%s model=%s approach=%s", input_path, model_name, approach)
    df = pd.read_csv(input_path)
    bundle, report = train_model_bundle(
        df=df,
        model_name=model_name,
        approach=approach,
        include_market_features=include_market_features,
    )
    save_model_bundle(bundle=bundle, output_path=model_output_path)
    save_training_report(report=report, output_path=report_output_path)

    training_df = prepare_training_frame(df=df, approach=approach)
    feature_columns = get_feature_columns(include_market_features=include_market_features)
    feature_frame = build_feature_frame(df=training_df, feature_columns=feature_columns)
    target_col = "target_direct_cover" if approach == "direct_cover" else "target_goal_diff"
    target = training_df[target_col].to_numpy()
    debug_feature_path, debug_group_path = export_feature_importance_debug(
        bundle=bundle,
        feature_frame=feature_frame,
        y=target,
    )

    LOGGER.info("Model train completed: model=%s report=%s", model_output_path, report_output_path)

    return (
        f"Model trained: {bundle.model_name} ({bundle.approach}) | "
        f"samples={report.sample_size} | "
        f"brier={report.brier_score} | log_loss={report.log_loss} | "
        f"model={model_output_path} | report={report_output_path} | "
        f"feature_importance={debug_feature_path} | feature_group_importance={debug_group_path}"
    )



def predict_command(
    input_path: Path,
    model_path: Path,
    output_path: Path,
) -> str:
    """Load trained model bundle and write prediction CSV for input features."""
    LOGGER.info("Prediction started: input=%s model=%s", input_path, model_path)
    df = pd.read_csv(input_path)
    bundle = load_model_bundle(model_path=model_path)
    prediction_df = generate_prediction_frame(bundle=bundle, df=df)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_df.to_csv(output_path, index=False)
    LOGGER.info("Prediction completed: rows=%s output=%s", len(prediction_df), output_path)
    return f"Predictions written: {len(prediction_df)} rows -> {output_path}"
