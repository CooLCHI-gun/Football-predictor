from pathlib import Path

import pandas as pd

from src.features.pipeline import build_feature_pipeline
from src.optimizer.grid_search import optimize_strategy


SAMPLE_RAW = Path("data/raw/sample_matches_phase3.csv")


def test_optimizer_writes_results_files(tmp_path: Path) -> None:
    feature_path = tmp_path / "features.csv"
    output_dir = tmp_path / "optimizer"
    build_feature_pipeline(input_path=SAMPLE_RAW, output_path=feature_path)

    message = optimize_strategy(
        input_path=feature_path,
        output_dir=output_dir,
        edge_grid=[0.02],
        confidence_grid=[0.55],
        policy_grid=["flat", "fractional_kelly", "vol_target"],
        kelly_grid=[0.25],
        max_stake_grid=[0.02],
        daily_exposure_grid=[0.05],
    )

    results_path = output_dir / "params_results.csv"
    best_path = output_dir / "best_params.json"

    assert results_path.exists()
    assert best_path.exists()
    assert "Optimization complete" in message

    result_df = pd.read_csv(results_path)
    assert not result_df.empty
    assert {"roi", "max_drawdown", "total_bets_placed", "score"}.issubset(result_df.columns)


def test_optimizer_dry_run_reports_run_count(tmp_path: Path) -> None:
    feature_path = tmp_path / "features.csv"
    output_dir = tmp_path / "optimizer"
    build_feature_pipeline(input_path=SAMPLE_RAW, output_path=feature_path)

    message = optimize_strategy(
        input_path=feature_path,
        output_dir=output_dir,
        edge_grid=[0.02, 0.03],
        confidence_grid=[0.55],
        policy_grid=["fractional_kelly", "vol_target"],
        kelly_grid=[0.25],
        max_stake_grid=[0.02],
        daily_exposure_grid=[0.03],
        max_runs=2,
        dry_run=True,
    )

    assert message == "Optimizer dry-run: would execute 2 runs."
    assert not (output_dir / "params_results.csv").exists()
