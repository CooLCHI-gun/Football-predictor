from pathlib import Path

import pandas as pd

from src.backtest.engine import run_backtest
from src.features.pipeline import build_feature_pipeline


SAMPLE_RAW = Path("data/raw/sample_matches_phase3.csv")


def test_walk_forward_backtest_writes_expected_artifacts(tmp_path: Path) -> None:
    feature_path = tmp_path / "features.csv"
    output_dir = tmp_path / "backtest"
    build_feature_pipeline(input_path=SAMPLE_RAW, output_path=feature_path)

    message = run_backtest(
        input_path=feature_path,
        output_dir=output_dir,
        model_name="rule_based",
        approach="direct_cover",
        include_market_features=True,
    )

    predictions_path = output_dir / "predictions.csv"
    trade_log_path = output_dir / "trade_log.csv"
    summary_path = output_dir / "summary.csv"

    assert predictions_path.exists()
    assert trade_log_path.exists()
    assert summary_path.exists()
    assert "Backtest complete" in message

    summary_df = pd.read_csv(summary_path)
    assert len(summary_df) == 1
    total_matches_evaluated = summary_df["total_matches_evaluated"].astype("float64").iloc[0]
    assert total_matches_evaluated > 0
    assert set(["win_rate", "roi", "max_drawdown", "total_bets_placed"]).issubset(summary_df.columns)
    assert set(["risk_of_ruin_estimate", "total_ev", "avg_ev_per_bet", "avg_clv_implied_edge"]).issubset(
        summary_df.columns
    )

    trade_log_df = pd.read_csv(trade_log_path)
    assert set(["expected_value", "expected_roi", "clv_implied_edge"]).issubset(trade_log_df.columns)
