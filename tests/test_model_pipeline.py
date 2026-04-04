from pathlib import Path

import pandas as pd

from src.features.pipeline import build_feature_pipeline
from src.models.baselines import generate_prediction_frame, train_model_bundle


SAMPLE_RAW = Path("data/raw/sample_matches_phase3.csv")


def test_logistic_training_and_prediction_pipeline(tmp_path: Path) -> None:
    feature_path = tmp_path / "features.csv"
    build_feature_pipeline(input_path=SAMPLE_RAW, output_path=feature_path)
    feature_df = pd.read_csv(feature_path)

    bundle, report = train_model_bundle(
        df=feature_df,
        model_name="logistic_regression",
        approach="direct_cover",
        include_market_features=True,
    )
    prediction_df = generate_prediction_frame(bundle=bundle, df=feature_df)

    assert report.sample_size >= 12
    assert report.brier_score is not None
    assert report.log_loss is not None
    assert prediction_df["home_cover_probability"].between(0.0, 1.0).all()
    assert prediction_df["away_cover_probability"].between(0.0, 1.0).all()
    assert set(["predicted_side", "model_probability", "confidence_score"]).issubset(prediction_df.columns)
