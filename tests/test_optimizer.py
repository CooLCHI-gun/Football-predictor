import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.config.settings import get_settings
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
    assert {
        "roi",
        "max_drawdown",
        "total_bets_placed",
        "score",
        "worst_window_roi",
        "roi_std",
        "win_rate_std",
    }.issubset(result_df.columns)


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


def test_optimizer_winrate_guarded_hard_min_bets_with_outer_rolling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    feature_path = tmp_path / "features_outer.csv"
    output_dir = tmp_path / "optimizer"

    # Outer rolling only needs enough rows to create sliding windows; backtest internals are mocked below.
    pd.DataFrame({"row_id": list(range(240))}).to_csv(feature_path, index=False)

    window_profiles = {
        0.01: [
            {"roi": 0.08, "win_rate": 0.62, "max_drawdown": 0.07, "total_bets_placed": 90},
            {"roi": 0.07, "win_rate": 0.61, "max_drawdown": 0.08, "total_bets_placed": 95},
            {"roi": 0.06, "win_rate": 0.60, "max_drawdown": 0.09, "total_bets_placed": 100},
        ],
        0.02: [
            {"roi": 0.04, "win_rate": 0.56, "max_drawdown": 0.08, "total_bets_placed": 130},
            {"roi": 0.03, "win_rate": 0.55, "max_drawdown": 0.09, "total_bets_placed": 128},
            {"roi": 0.035, "win_rate": 0.57, "max_drawdown": 0.10, "total_bets_placed": 132},
        ],
    }

    def _fake_run_backtest_with_result(
        *,
        input_path: Path,
        strategy_overrides: dict[str, object],
        **_: object,
    ) -> SimpleNamespace:
        edge = float(strategy_overrides["min_edge_threshold"])
        file_name = input_path.name
        window_idx = 0
        if file_name.startswith("outer_window_") and file_name.endswith(".csv"):
            token = file_name.removeprefix("outer_window_").removesuffix(".csv")
            window_idx = max(0, int(token) - 1)

        summary = {
            **window_profiles[edge][window_idx],
            "risk_of_ruin_estimate": 0.05,
            "avg_clv_pct": 0.0,
            "median_clv_pct": 0.0,
            "pct_positive_clv": 0.0,
            "prediction_cache_hits": 0,
            "prediction_cache_misses": 1,
        }
        return SimpleNamespace(summary=summary)

    monkeypatch.setattr("src.optimizer.grid_search.run_backtest_with_result", _fake_run_backtest_with_result)
    monkeypatch.setenv("OPTIMIZER_MODE", "WINRATE_GUARDED")
    monkeypatch.setenv("OPTIMIZER_HARD_MIN_BETS", "120")
    monkeypatch.setenv("OPTIMIZER_MIN_BETS_TARGET", "120")
    monkeypatch.setenv("OPTIMIZER_WINRATE_MIN_WIN_RATE", "0.53")
    monkeypatch.setenv("OPTIMIZER_WINRATE_DRAWDOWN_CAP", "0.12")
    get_settings.cache_clear()

    try:
        optimize_strategy(
            input_path=feature_path,
            output_dir=output_dir,
            edge_grid=[0.01, 0.02],
            confidence_grid=[0.55],
            policy_grid=["flat"],
            kelly_grid=[0.15],
            max_alerts_grid=[1],
            max_stake_grid=[0.01],
            daily_exposure_grid=[0.03],
            outer_rolling_windows=3,
            outer_min_window_matches=60,
            max_runs=2,
        )
    finally:
        get_settings.cache_clear()

    results_df = pd.read_csv(output_dir / "params_results.csv")
    best_payload = json.loads((output_dir / "best_params.json").read_text(encoding="utf-8"))

    assert set(results_df["outer_rolling_windows"]) == {3}
    assert set(results_df["min_window_bets"]) == {90, 128}
    assert float(best_payload["min_edge_threshold"]) == 0.02
    assert int(best_payload["min_window_bets"]) == 128
    assert float(best_payload["score"]) > -1000.0


def test_optimizer_balanced_guarded_prefers_stable_positive_roi_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature_path = tmp_path / "features_balanced.csv"
    output_dir = tmp_path / "optimizer"
    pd.DataFrame({"row_id": list(range(240))}).to_csv(feature_path, index=False)

    window_profiles = {
        0.01: [
            {"roi": 0.06, "win_rate": 0.59, "max_drawdown": 0.09, "total_bets_placed": 118},
            {"roi": -0.01, "win_rate": 0.50, "max_drawdown": 0.10, "total_bets_placed": 115},
            {"roi": 0.07, "win_rate": 0.60, "max_drawdown": 0.11, "total_bets_placed": 116},
        ],
        0.02: [
            {"roi": 0.045, "win_rate": 0.57, "max_drawdown": 0.08, "total_bets_placed": 126},
            {"roi": 0.042, "win_rate": 0.56, "max_drawdown": 0.09, "total_bets_placed": 124},
            {"roi": 0.044, "win_rate": 0.58, "max_drawdown": 0.09, "total_bets_placed": 125},
        ],
    }

    def _fake_run_backtest_with_result(
        *,
        input_path: Path,
        strategy_overrides: dict[str, object],
        **_: object,
    ) -> SimpleNamespace:
        edge = float(strategy_overrides["min_edge_threshold"])
        file_name = input_path.name
        window_idx = 0
        if file_name.startswith("outer_window_") and file_name.endswith(".csv"):
            token = file_name.removeprefix("outer_window_").removesuffix(".csv")
            window_idx = max(0, int(token) - 1)
        summary = {
            **window_profiles[edge][window_idx],
            "risk_of_ruin_estimate": 0.05,
            "avg_clv_pct": 0.0,
            "median_clv_pct": 0.0,
            "pct_positive_clv": 0.0,
            "prediction_cache_hits": 0,
            "prediction_cache_misses": 1,
        }
        return SimpleNamespace(summary=summary)

    monkeypatch.setattr("src.optimizer.grid_search.run_backtest_with_result", _fake_run_backtest_with_result)
    monkeypatch.setenv("OPTIMIZER_MODE", "BALANCED_GUARDED")
    monkeypatch.setenv("OPTIMIZER_HARD_MIN_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_WINDOW_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_DRAWDOWN_CAP", "0.12")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_ROI", "0.0")
    get_settings.cache_clear()

    try:
        optimize_strategy(
            input_path=feature_path,
            output_dir=output_dir,
            edge_grid=[0.01, 0.02],
            confidence_grid=[0.55],
            policy_grid=["flat"],
            kelly_grid=[0.15],
            max_alerts_grid=[1],
            max_stake_grid=[0.01],
            daily_exposure_grid=[0.03],
            outer_rolling_windows=3,
            outer_min_window_matches=60,
            max_runs=2,
        )
    finally:
        get_settings.cache_clear()

    best_payload = json.loads((output_dir / "best_params.json").read_text(encoding="utf-8"))
    assert float(best_payload["min_edge_threshold"]) == 0.02
    assert float(best_payload["worst_window_roi"]) > 0.0
    assert float(best_payload["roi_std"]) < 0.01


def test_optimizer_balanced_guarded_marks_fallback_winner_when_all_runs_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature_path = tmp_path / "features_fallback.csv"
    output_dir = tmp_path / "optimizer"
    pd.DataFrame({"row_id": list(range(180))}).to_csv(feature_path, index=False)

    def _fake_run_backtest_with_result(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            summary={
                "roi": -0.02,
                "win_rate": 0.52,
                "max_drawdown": 0.15,
                "total_bets_placed": 88,
                "risk_of_ruin_estimate": 0.08,
                "avg_clv_pct": 0.0,
                "median_clv_pct": 0.0,
                "pct_positive_clv": 0.0,
                "prediction_cache_hits": 0,
                "prediction_cache_misses": 1,
            }
        )

    monkeypatch.setattr("src.optimizer.grid_search.run_backtest_with_result", _fake_run_backtest_with_result)
    monkeypatch.setenv("OPTIMIZER_MODE", "BALANCED_GUARDED")
    monkeypatch.setenv("OPTIMIZER_HARD_MIN_BETS", "50")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_WINDOW_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_DRAWDOWN_CAP", "0.12")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_ROI", "0.0")
    get_settings.cache_clear()

    try:
        optimize_strategy(
            input_path=feature_path,
            output_dir=output_dir,
            edge_grid=[0.01],
            confidence_grid=[0.55],
            policy_grid=["flat"],
            kelly_grid=[0.15],
            max_alerts_grid=[1],
            max_stake_grid=[0.01],
            daily_exposure_grid=[0.03],
            outer_rolling_windows=1,
            outer_min_window_matches=60,
            max_runs=1,
        )
    finally:
        get_settings.cache_clear()

    best_payload = json.loads((output_dir / "best_params.json").read_text(encoding="utf-8"))
    assert best_payload["selection_reason"] == "fallback_best_score"
    assert best_payload["passed_guardrails"] is False
