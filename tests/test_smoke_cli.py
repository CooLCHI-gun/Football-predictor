import inspect
from pathlib import Path

from typer.testing import CliRunner
from typer.models import OptionInfo

from src.features.pipeline import build_feature_pipeline
from src.main import _resolve_output_dir, app, backtest, optimize, predict, train


def test_cli_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "HKJC-oriented" in result.stdout


def test_build_features_full_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["build-features-full", "--help"])
    assert result.exit_code == 0


def test_predict_full_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["predict-full", "--help"])
    assert result.exit_code == 0


def test_optimize_help_mentions_safety_options() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["optimize", "--help"])
    assert result.exit_code == 0
    assert "--max-runs" in result.stdout
    assert "--dry-run" in result.stdout


def test_optimize_dry_run_succeeds_when_optimizer_artifacts_exist(tmp_path: Path) -> None:
    feature_path = tmp_path / "features.csv"
    build_feature_pipeline(input_path=Path("data/raw/sample_matches_phase3.csv"), output_path=feature_path)

    optimizer_dir = tmp_path / "optimizer"
    optimizer_dir.mkdir(parents=True, exist_ok=True)
    (optimizer_dir / "params_results.csv").write_text("dummy", encoding="utf-8")
    (optimizer_dir / "best_params.json").write_text("{}", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "optimize",
            "--input-path",
            str(feature_path),
            "--output-dir",
            str(optimizer_dir),
            "--dry-run",
            "--max-runs",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Optimizer dry-run: would execute" in result.stdout


def test_download_real_data_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["download-real-data", "--help"])
    assert result.exit_code == 0


def test_phase3_commands_default_to_phase3_feature_csv() -> None:
    expected_default = Path("data/processed/features_phase3.csv")

    train_default = inspect.signature(train).parameters["input_path"].default
    predict_default = inspect.signature(predict).parameters["input_path"].default
    backtest_default = inspect.signature(backtest).parameters["input_path"].default

    assert isinstance(train_default, OptionInfo) and train_default.default == expected_default
    assert isinstance(predict_default, OptionInfo) and predict_default.default == expected_default
    assert isinstance(backtest_default, OptionInfo) and backtest_default.default == expected_default


def test_backtest_and_optimize_expose_run_id_option() -> None:
    backtest_run_id = inspect.signature(backtest).parameters["run_id"].default
    optimize_run_id = inspect.signature(optimize).parameters["run_id"].default

    assert isinstance(backtest_run_id, OptionInfo)
    assert isinstance(optimize_run_id, OptionInfo)


def test_resolve_output_dir_uses_run_id_subdirectory() -> None:
    assert _resolve_output_dir(Path("artifacts/backtest"), None) == Path("artifacts/backtest")
    assert _resolve_output_dir(Path("artifacts/backtest"), "20260403_pm1") == Path("artifacts/backtest/20260403_pm1")
