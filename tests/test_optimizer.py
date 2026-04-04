from pathlib import Path

import pandas as pd

from src.features.pipeline import build_feature_pipeline
from src.optimizer.grid_search import ObjectiveWeights, compute_objective_score, optimize_strategy


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


def test_multi_objective_score_rewards_win_rate_and_placed_bets() -> None:
    weights = ObjectiveWeights(
        lambda_drawdown=0.5,
        lambda_ror=0.7,
        mu_clv=0.3,
        mu_win_rate=0.4,
        mu_placed_bets=0.5,
        target_placed_bets=100,
        lambda_low_bets=0.1,
        min_bets_target=10,
    )

    low_quality = compute_objective_score(
        roi=0.03,
        win_rate=0.48,
        max_drawdown=0.10,
        risk_of_ruin_estimate=0.15,
        clv_score=0.0,
        total_bets_placed=20,
        weights=weights,
    )
    better_quality = compute_objective_score(
        roi=0.03,
        win_rate=0.58,
        max_drawdown=0.10,
        risk_of_ruin_estimate=0.15,
        clv_score=0.0,
        total_bets_placed=100,
        weights=weights,
    )

    assert better_quality > low_quality
