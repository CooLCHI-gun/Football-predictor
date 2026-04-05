import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from src.alerts.telegram import send_telegram_alert
from src.alerts.telegram_client import TelegramClient, validate_telegram_configuration
from src.backtest.hkjc_analysis import read_summary_and_analyze
from src.backtest.hkjc_history_analysis import evaluate_hkjc_model_on_history, format_hkjc_history_evaluation
from src.backtest.engine import run_backtest
from src.config.settings import get_settings
from src.data.database import ensure_sqlite_parent_dir, get_engine
from src.data.models import Base
from src.features.pipeline import build_feature_pipeline
from src.models.pipeline import predict_command, train_model_command
from src.optimizer.grid_search import optimize_strategy
from src.providers.csv_provider import LocalCSVProvider
from src.providers.football_data_provider import DEFAULT_FOOTBALL_DATA_URLS, download_and_normalize_football_data
from src.live_feed.clients import build_market_feed_client
from src.live_feed.providers.hkjc_request_debug import inspect_request_sources, report_path_for_mode, replay_request_candidate, summarize_candidate, write_inspection_report
from src.live_feed.providers.hkjc_result_validator import validate_results_snapshot
from src.live_feed.providers.hkjc_provider import HKJCFootballProvider
from src.services.results_validation import build_results_validation_report, resolve_results_detail_fb_odds_types
from src.services.hkjc_history import collect_hkjc_history
from src.services.live_runner import LiveRunner, LiveRunnerConfig
from src.utils.logging import configure_logging

app = typer.Typer(help="HKJC-oriented football handicap research framework")

CLI_DEFAULT_PHASE3_FEATURES = Path("data/processed/features_phase3.csv")
CLI_DEFAULT_PHASE3_FULL_FEATURES = Path("data/processed/features_phase3_full.csv")


def _parse_hhmm(value: str, option_name: str) -> tuple[int, int]:
    candidate = value.strip()
    parts = candidate.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise typer.BadParameter(f"{option_name} must be HH:MM format, got: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise typer.BadParameter(f"{option_name} must be within 00:00..23:59, got: {value}")
    return hour, minute


def _wait_until_local_time(target_hour: int, target_minute: int, tz: ZoneInfo, label: str, skip_wait: bool) -> None:
    now_local = datetime.now(tz)
    target_local = now_local.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target_local <= now_local:
        typer.echo(f"[{label}] target time already passed for today ({target_local.strftime('%Y-%m-%d %H:%M %Z')}); run now.")
        return

    wait_seconds = int((target_local - now_local).total_seconds())
    typer.echo(f"[{label}] scheduled at {target_local.strftime('%Y-%m-%d %H:%M %Z')} (wait {wait_seconds}s).")
    if skip_wait:
        typer.echo(f"[{label}] --skip-wait enabled; run immediately.")
        return

    while True:
        remaining = int((target_local - datetime.now(tz)).total_seconds())
        if remaining <= 0:
            break
        time.sleep(min(remaining, 60))


def _guard_output_file(path: Path, force: bool, label: str) -> None:
    if path.exists() and not force:
        raise typer.BadParameter(f"{label} already exists: {path}. Use --force to overwrite.")


def _guard_output_dir_files(output_dir: Path, file_names: list[str], force: bool, label: str) -> None:
    existing = [name for name in file_names if (output_dir / name).exists()]
    if existing and not force:
        joined = ", ".join(existing)
        raise typer.BadParameter(
            f"{label} would overwrite existing files in {output_dir}: {joined}. Use --force to overwrite."
        )


def _resolve_output_dir(base_output_dir: Path, run_id: str | None) -> Path:
    normalized_run_id = (run_id or "").strip()
    if not normalized_run_id:
        return base_output_dir
    if any(token in normalized_run_id for token in ("/", "\\", "..")):
        raise typer.BadParameter("run-id must not contain path separators or '..'.")
    return base_output_dir / normalized_run_id


def _reset_output_dir_files(output_dir: Path, file_names: list[str]) -> None:
    for file_name in file_names:
        target = output_dir / file_name
        if target.exists():
            target.unlink()


@app.callback()
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


@app.command("init-db")
def init_db() -> None:
    """Initialize SQLite schema for Phase 1."""
    sqlite_file = ensure_sqlite_parent_dir()
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    if sqlite_file is not None:
        typer.echo(f"Database initialized at: {sqlite_file}")
    else:
        typer.echo("Database initialized.")


@app.command()
def ingest(raw_dir: Path = Path("data/raw")) -> None:
    """Validate Phase 1 CSV templates from raw_dir."""
    provider = LocalCSVProvider()
    errors = provider.validate_templates(raw_dir)
    if errors:
        for error in errors:
            typer.echo(f"[ERROR] {error}")
        raise typer.Exit(code=1)
    typer.echo("CSV template validation passed.")


@app.command("build-features")
def build_features(
    input_path: Path = typer.Option(Path("data/raw/matches_template.csv"), help="Input raw CSV path."),
    output_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FEATURES, help="Output feature CSV path."),
    feature_field_config_path: Path | None = typer.Option(
        None,
        envvar="FEATURE_FIELD_CONFIG_PATH",
        help="Optional feature field config JSON path (selection/order/missing strategy).",
    ),
    force: bool = typer.Option(False, help="Overwrite output file if it exists."),
) -> None:
    """Build Phase 2/3 features using strict as-of logic."""
    settings = get_settings()
    resolved_output = settings.phase3_feature_csv_path if output_path == CLI_DEFAULT_PHASE3_FEATURES else output_path
    resolved_feature_field_config = feature_field_config_path or settings.feature_field_config_path
    _guard_output_file(resolved_output, force=force, label="Feature output")
    typer.echo(
        build_feature_pipeline(
            input_path=input_path,
            output_path=resolved_output,
            feature_field_config_path=resolved_feature_field_config,
        )
    )


@app.command("build-features-full")
def build_features_full(
    input_path: Path = typer.Option(
        Path("data/raw/real/historical_matches_real_non_hkjc.csv"),
        help="Input NON_HKJC normalized raw CSV path.",
    ),
    output_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FULL_FEATURES, help="Output Phase 3 full feature CSV path."),
    feature_field_config_path: Path | None = typer.Option(
        None,
        envvar="FEATURE_FIELD_CONFIG_PATH",
        help="Optional feature field config JSON path (selection/order/missing strategy).",
    ),
    force: bool = typer.Option(False, help="Overwrite output file if it exists."),
) -> None:
    """Build Phase 3 full feature file for 100+ real-match backtests."""
    settings = get_settings()
    resolved_input = (
        settings.phase3_full_raw_csv_path if input_path == Path("data/raw/real/historical_matches_real_non_hkjc.csv") else input_path
    )
    resolved_output = (
        settings.phase3_full_feature_csv_path if output_path == CLI_DEFAULT_PHASE3_FULL_FEATURES else output_path
    )
    resolved_feature_field_config = feature_field_config_path or settings.feature_field_config_path
    _guard_output_file(resolved_output, force=force, label="Full feature output")
    typer.echo(
        build_feature_pipeline(
            input_path=resolved_input,
            output_path=resolved_output,
            feature_field_config_path=resolved_feature_field_config,
        )
    )


@app.command("download-real-data")
def download_real_data(
    urls: str | None = typer.Option(None, help="Comma-separated football-data source URLs."),
    raw_dir: Path = typer.Option(Path("data/raw/real"), help="Directory for raw downloaded CSV files."),
    normalized_output_path: Path = typer.Option(
        Path("data/raw/real/historical_matches_real_non_hkjc.csv"),
        help="Normalized raw output CSV path.",
    ),
    feature_output_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FULL_FEATURES, help="Output Phase 3 full feature CSV path."),
    force: bool = typer.Option(False, help="Overwrite normalized/feature outputs if they exist."),
) -> None:
    """Download NON_HKJC real historical data and prepare Phase 3 full features."""
    settings = get_settings()
    source_urls = [item.strip() for item in (urls or settings.football_data_source_urls).split(",") if item.strip()]
    if not source_urls:
        source_urls = DEFAULT_FOOTBALL_DATA_URLS

    resolved_feature_output = (
        settings.phase3_full_feature_csv_path if feature_output_path == CLI_DEFAULT_PHASE3_FULL_FEATURES else feature_output_path
    )
    _guard_output_file(normalized_output_path, force=force, label="Normalized raw output")
    _guard_output_file(resolved_feature_output, force=force, label="Full feature output")

    summary = download_and_normalize_football_data(
        urls=source_urls,
        raw_output_dir=raw_dir,
        normalized_output_path=normalized_output_path,
    )

    build_message = build_feature_pipeline(
        input_path=summary.normalized_output_path,
        output_path=resolved_feature_output,
        feature_field_config_path=settings.feature_field_config_path,
    )

    import pandas as pd

    feature_rows = len(pd.read_csv(resolved_feature_output)) if resolved_feature_output.exists() else 0
    mvp_met = "YES" if feature_rows >= 100 else "NO"

    typer.echo("Real data import complete (NON_HKJC).")
    typer.echo(f"Source URLs: {', '.join(summary.source_urls)}")
    typer.echo(f"Raw rows downloaded: {summary.raw_rows_downloaded}")
    typer.echo(f"Normalized matches retained: {summary.normalized_matches_retained}")
    typer.echo(f"Final feature row count: {feature_rows}")
    typer.echo(f"MVP 100-match threshold met: {mvp_met}")
    typer.echo(f"Normalized raw file: {summary.normalized_output_path}")
    typer.echo(f"Feature file: {resolved_feature_output}")
    typer.echo(f"Build summary: {build_message}")


@app.command()
def train(
    input_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FEATURES, help="Input feature CSV path."),
    model_name: str | None = typer.Option(None, help="Model name override (e.g., logistic_regression)."),
    approach: str | None = typer.Option(None, help="Approach override (direct_cover or goal_diff)."),
    include_market_features: bool | None = typer.Option(None, help="Override whether market features are included."),
    model_output_path: Path = typer.Option(Path("artifacts/model_bundle.pkl"), help="Output trained model bundle path."),
    report_output_path: Path = typer.Option(Path("artifacts/train_report.json"), help="Output training report JSON path."),
    proxy_alert_missing_rate_threshold: float | None = typer.Option(
        None,
        help="Proxy monitor missing-rate alert threshold override.",
    ),
    proxy_alert_consecutive_runs: int | None = typer.Option(
        None,
        help="Proxy monitor consecutive runs required before alerting.",
    ),
    force: bool = typer.Option(False, help="Overwrite existing model/report artifacts."),
) -> None:
    """Train a Phase 3 baseline model on a feature CSV."""
    settings = get_settings()
    _guard_output_file(model_output_path, force=force, label="Model output")
    _guard_output_file(report_output_path, force=force, label="Training report output")
    typer.echo(
        train_model_command(
            input_path=settings.phase3_feature_csv_path if input_path == CLI_DEFAULT_PHASE3_FEATURES else input_path,
            model_name=model_name or settings.model_name,
            approach=approach or settings.model_approach,
            include_market_features=settings.include_market_features if include_market_features is None else include_market_features,
            model_output_path=model_output_path,
            report_output_path=report_output_path,
            proxy_alert_missing_rate_threshold=(
                settings.proxy_alert_missing_rate_threshold
                if proxy_alert_missing_rate_threshold is None
                else proxy_alert_missing_rate_threshold
            ),
            proxy_alert_consecutive_runs=(
                settings.proxy_alert_consecutive_runs
                if proxy_alert_consecutive_runs is None
                else proxy_alert_consecutive_runs
            ),
        )
    )


@app.command()
def backtest(
    input_csv_path: Path = typer.Option(
        CLI_DEFAULT_PHASE3_FEATURES,
        "--input-csv-path",
        "--input-path",
        help="Phase 3 feature CSV path for backtest (expects pipeline feature CSV, not YAML config).",
    ),
    output_dir: Path = typer.Option(Path("artifacts/backtest"), help="Backtest artifact output directory."),
    model_name: str | None = typer.Option(None, help="Model name override for walk-forward training."),
    approach: str | None = typer.Option(None, help="Approach override for walk-forward training."),
    include_market_features: bool | None = typer.Option(None, help="Override market feature usage for walk-forward training."),
    flip_hkjc_side: bool | None = typer.Option(
        None,
        "--flip-hkjc-side/--no-flip-hkjc-side",
        help="HKJC-only side flip switch. CLI overrides FLIP_HKJC_SIDE when explicitly set.",
    ),
    run_id: str | None = typer.Option(None, help="Optional run identifier; writes to artifacts/backtest/<run-id> when provided."),
    force: bool = typer.Option(False, help="Overwrite existing backtest artifacts in output directory."),
) -> None:
    """Run walk-forward backtest using Phase 3 model pipeline."""
    settings = get_settings()
    resolved_output_dir = _resolve_output_dir(output_dir, run_id)
    _guard_output_dir_files(
        output_dir=resolved_output_dir,
        file_names=["predictions.csv", "trade_log.csv", "summary.csv"],
        force=force,
        label="Backtest",
    )
    typer.echo(
        run_backtest(
            input_path=(
                settings.phase3_feature_csv_path
                if input_csv_path == CLI_DEFAULT_PHASE3_FEATURES
                else input_csv_path
            ),
            output_dir=output_dir,
            model_name=model_name,
            approach=approach,
            include_market_features=include_market_features,
            flip_hkjc_side=flip_hkjc_side,
            run_id=run_id,
        )
    )


@app.command()
def optimize(
    input_csv_path: Path = typer.Option(
        CLI_DEFAULT_PHASE3_FULL_FEATURES,
        "--input-csv-path",
        "--input-path",
        help="Phase 3 full feature CSV path for optimization (expects pipeline feature CSV, not YAML config).",
    ),
    output_dir: Path = typer.Option(Path("artifacts/optimizer"), help="Optimizer output directory."),
    edge_grid: str = typer.Option("0.01,0.02,0.03,0.04", help="Comma-separated grid for min_edge_threshold."),
    confidence_grid: str = typer.Option("0.50,0.53,0.56,0.60", help="Comma-separated grid for min_confidence_threshold."),
    max_alerts_grid: str = typer.Option("1,2,3", help="Comma-separated grid for max alerts per cycle (mapped to max_concurrent_bets)."),
    policy_grid: str = typer.Option(
        "flat,fractional_kelly,vol_target",
        help="Comma-separated stake policies (flat,fixed_fraction,fractional_kelly,vol_target).",
    ),
    kelly_grid: str = typer.Option("0.15,0.25,0.35,0.50", help="Comma-separated grid for fractional Kelly factor."),
    max_stake_grid: str = typer.Option("0.01,0.02", help="Comma-separated grid for max stake percent."),
    daily_exposure_grid: str = typer.Option("0.03,0.05", help="Comma-separated grid for daily max exposure percent."),
    run_id: str | None = typer.Option(None, help="Optional run identifier; writes to artifacts/optimizer/<run-id> when provided."),
    use_prediction_cache: bool = typer.Option(
        False,
        "--use-prediction-cache/--no-prediction-cache",
        help="Reuse fold prediction cache during optimization runs.",
    ),
    refresh_prediction_cache: bool = typer.Option(
        False,
        "--refresh-prediction-cache",
        help="Force refresh of cached fold predictions before strategy replay.",
    ),
    max_runs: int | None = typer.Option(
        None,
        "--max-runs",
        min=1,
        help="Optional cap on number of parameter runs executed.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate grid and print run count without executing backtests.",
    ),
    force: bool = typer.Option(False, help="Overwrite optimizer summary artifacts if they exist."),
) -> None:
    """Run walk-forward grid-search optimization on strategy and bankroll parameters."""
    resolved_output_dir = _resolve_output_dir(output_dir, run_id)
    if not dry_run:
        _guard_output_dir_files(
            output_dir=resolved_output_dir,
            file_names=["params_results.csv", "best_params.json"],
            force=force,
            label="Optimizer",
        )
    typer.echo(
        optimize_strategy(
            input_path=input_csv_path,
            output_dir=output_dir,
            prediction_cache_dir=Path("artifacts/cache/backtest_predictions"),
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=refresh_prediction_cache,
            run_id=run_id,
            edge_grid=[float(item.strip()) for item in edge_grid.split(",") if item.strip()],
            confidence_grid=[float(item.strip()) for item in confidence_grid.split(",") if item.strip()],
            max_alerts_grid=[int(item.strip()) for item in max_alerts_grid.split(",") if item.strip()],
            policy_grid=[item.strip() for item in policy_grid.split(",") if item.strip()],
            kelly_grid=[float(item.strip()) for item in kelly_grid.split(",") if item.strip()],
            max_stake_grid=[float(item.strip()) for item in max_stake_grid.split(",") if item.strip()],
            daily_exposure_grid=[float(item.strip()) for item in daily_exposure_grid.split(",") if item.strip()],
            max_runs=max_runs,
            dry_run=dry_run,
        )
    )


@app.command("daily-maintenance")
def daily_maintenance(
    backtest_time: str = typer.Option("01:30", help="Daily backtest time in HH:MM."),
    optimize_time: str = typer.Option("03:30", help="Daily optimizer time in HH:MM."),
    timezone_name: str = typer.Option("Asia/Hong_Kong", help="IANA timezone name for schedule."),
    backtest_input_csv_path: Path = typer.Option(
        CLI_DEFAULT_PHASE3_FULL_FEATURES,
        "--backtest-input-csv-path",
        "--backtest-input-path",
        help="Feature CSV path used by daily backtest.",
    ),
    optimize_input_csv_path: Path = typer.Option(
        CLI_DEFAULT_PHASE3_FULL_FEATURES,
        "--optimize-input-csv-path",
        "--optimize-input-path",
        help="Feature CSV path used by daily optimizer.",
    ),
    backtest_output_dir: Path = typer.Option(Path("artifacts/backtest"), help="Backtest base output directory."),
    optimize_output_dir: Path = typer.Option(Path("artifacts/optimizer"), help="Optimizer base output directory."),
    use_date_run_id: bool = typer.Option(
        True,
        "--use-date-run-id/--no-date-run-id",
        help="When enabled, auto-write outputs into date-scoped run-id folders.",
    ),
    backtest_run_id: str | None = typer.Option(None, help="Override backtest run-id."),
    optimize_run_id: str | None = typer.Option(None, help="Override optimizer run-id."),
    use_prediction_cache: bool = typer.Option(
        True,
        "--use-prediction-cache/--no-prediction-cache",
        help="Reuse fold prediction cache during optimizer runs.",
    ),
    refresh_prediction_cache: bool = typer.Option(
        False,
        "--refresh-prediction-cache",
        help="Force refresh of cached fold predictions before optimizer replay.",
    ),
    max_runs: int | None = typer.Option(None, help="Optional cap on optimizer parameter runs."),
    dry_run_optimize: bool = typer.Option(False, help="Validate optimizer grid without execution."),
    repeat_daily: bool = typer.Option(False, "--repeat-daily/--run-once", help="Repeat schedule every day in same process."),
    force: bool = typer.Option(False, help="Overwrite same-run artifact files when needed."),
    skip_wait: bool = typer.Option(False, help="Skip waiting and run both tasks immediately (for tests/debug)."),
) -> None:
    """Run daily backtest and optimizer in one process at two local times."""
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise typer.BadParameter(f"Invalid timezone: {timezone_name}") from exc

    backtest_hour, backtest_minute = _parse_hhmm(backtest_time, "--backtest-time")
    optimize_hour, optimize_minute = _parse_hhmm(optimize_time, "--optimize-time")
    if repeat_daily and skip_wait:
        raise typer.BadParameter("--repeat-daily cannot be used with --skip-wait.")

    while True:
        local_date = datetime.now(tz).strftime("%Y%m%d")
        resolved_backtest_run_id = backtest_run_id
        resolved_optimize_run_id = optimize_run_id
        if use_date_run_id:
            if resolved_backtest_run_id is None:
                resolved_backtest_run_id = f"daily_backtest_{local_date}"
            if resolved_optimize_run_id is None:
                resolved_optimize_run_id = f"daily_optimize_{local_date}"

        typer.echo(
            "\n".join(
                [
                    "Daily maintenance schedule initialized.",
                    f"timezone={timezone_name}",
                    f"backtest_time={backtest_time}",
                    f"optimize_time={optimize_time}",
                    f"backtest_run_id={resolved_backtest_run_id}",
                    f"optimize_run_id={resolved_optimize_run_id}",
                ]
            )
        )

        _wait_until_local_time(backtest_hour, backtest_minute, tz, label="backtest", skip_wait=skip_wait)

        resolved_backtest_output_dir = _resolve_output_dir(backtest_output_dir, resolved_backtest_run_id)
        _guard_output_dir_files(
            output_dir=resolved_backtest_output_dir,
            file_names=["predictions.csv", "trade_log.csv", "summary.csv"],
            force=force,
            label="Daily backtest",
        )
        typer.echo(
            run_backtest(
                input_path=backtest_input_csv_path,
                output_dir=backtest_output_dir,
                model_name=None,
                approach=None,
                include_market_features=None,
                flip_hkjc_side=None,
                run_id=resolved_backtest_run_id,
            )
        )

        _wait_until_local_time(optimize_hour, optimize_minute, tz, label="optimize", skip_wait=skip_wait)

        resolved_optimize_output_dir = _resolve_output_dir(optimize_output_dir, resolved_optimize_run_id)
        if not dry_run_optimize:
            _guard_output_dir_files(
                output_dir=resolved_optimize_output_dir,
                file_names=["params_results.csv", "best_params.json"],
                force=force,
                label="Daily optimizer",
            )
        typer.echo(
            optimize_strategy(
                input_path=optimize_input_csv_path,
                output_dir=optimize_output_dir,
                prediction_cache_dir=Path("artifacts/cache/backtest_predictions"),
                use_prediction_cache=use_prediction_cache,
                force_prediction_cache_refresh=refresh_prediction_cache,
                run_id=resolved_optimize_run_id,
                edge_grid=[0.01, 0.02, 0.03, 0.04],
                confidence_grid=[0.50, 0.53, 0.56, 0.60],
                max_alerts_grid=[1, 2, 3],
                policy_grid=["flat", "fractional_kelly", "vol_target"],
                kelly_grid=[0.15, 0.25, 0.35, 0.50],
                max_stake_grid=[0.01, 0.02],
                daily_exposure_grid=[0.03, 0.05],
                max_runs=max_runs,
                dry_run=dry_run_optimize,
            )
        )
        typer.echo("Daily maintenance completed.")

        if not repeat_daily:
            break

        next_cycle_local = (datetime.now(tz) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        typer.echo(f"[scheduler] next daily cycle at {next_cycle_local.strftime('%Y-%m-%d %H:%M %Z')}")
        while True:
            remaining = int((next_cycle_local - datetime.now(tz)).total_seconds())
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))


@app.command("analyze-hkjc")
def analyze_hkjc(
    summary_csv_path: Path = typer.Option(
        Path("artifacts/backtest/summary.csv"),
        help="Backtest summary CSV path (recommended: HKJC-only run summary).",
    ),
) -> None:
    """Analyze HKJC backtest summary and print CLV+ROI-based threshold/stake recommendation."""
    if not summary_csv_path.exists():
        raise typer.BadParameter(f"Summary CSV not found: {summary_csv_path}")

    recommendation = read_summary_and_analyze(str(summary_csv_path))
    typer.echo("HKJC summary analysis")
    typer.echo(f"status={recommendation.status}")
    typer.echo(f"recommendation={recommendation.recommendation}")
    typer.echo("rationale:")
    for item in recommendation.rationale:
        typer.echo(f"- {item}")


@app.command("analyze-hkjc-history")
def analyze_hkjc_history(
    prediction_path: Path = typer.Option(
        Path("artifacts/predictions/hkjc_history_predictions.csv"),
        help="HKJC prediction CSV path generated by predict command.",
    ),
    flip_side: bool = typer.Option(
        False,
        "--flip-side",
        help="Apply flipped-side as active decision mode while still reporting base vs flipped metrics.",
    ),
) -> None:
    """Evaluate HKJC prediction hit rate, flip impact, and optional flat-stake ROI diagnostics."""
    if not prediction_path.exists():
        raise typer.BadParameter(f"Prediction CSV not found: {prediction_path}")

    import pandas as pd

    prediction_df = pd.read_csv(prediction_path)
    result = evaluate_hkjc_model_on_history(prediction_df=prediction_df, flip_side=flip_side)
    for line in format_hkjc_history_evaluation(result):
        typer.echo(line)


@app.command()
def predict(
    input_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FEATURES, help="Input feature CSV path."),
    model_path: Path = typer.Option(Path("artifacts/model_bundle.pkl"), help="Trained model bundle path."),
    output_path: Path = typer.Option(Path("artifacts/predictions.csv"), help="Prediction output CSV path."),
    force: bool = typer.Option(False, help="Overwrite existing prediction CSV."),
) -> None:
    """Generate prediction probabilities from a trained Phase 3 model."""
    settings = get_settings()
    _guard_output_file(output_path, force=force, label="Prediction output")
    typer.echo(
        predict_command(
            input_path=settings.phase3_feature_csv_path if input_path == CLI_DEFAULT_PHASE3_FEATURES else input_path,
            model_path=model_path,
            output_path=output_path,
        )
    )


@app.command("predict-full")
def predict_full(
    input_path: Path = typer.Option(CLI_DEFAULT_PHASE3_FULL_FEATURES, help="Input full feature CSV path."),
    model_path: Path = typer.Option(Path("artifacts/model_bundle.pkl"), help="Trained model bundle path."),
    output_path: Path = typer.Option(Path("artifacts/predictions_full.csv"), help="Prediction output CSV path."),
    force: bool = typer.Option(False, help="Overwrite existing prediction CSV."),
) -> None:
    """Generate predictions from the full (100+ matches) feature dataset."""
    settings = get_settings()
    resolved_input = settings.phase3_full_feature_csv_path if input_path == CLI_DEFAULT_PHASE3_FULL_FEATURES else input_path
    _guard_output_file(output_path, force=force, label="Prediction output")
    typer.echo(
        predict_command(
            input_path=resolved_input,
            model_path=model_path,
            output_path=output_path,
        )
    )


@app.command()
def alert(
    predictions_path: Path = typer.Option(Path("artifacts/predictions_full.csv"), help="Prediction CSV path."),
    edge_threshold: float | None = typer.Option(None, help="Override edge threshold for alert filtering."),
    confidence_threshold: float | None = typer.Option(None, help="Override confidence threshold for alert filtering."),
    max_alerts: int = typer.Option(3, help="Maximum alerts to send."),
    flip_hkjc_side: bool | None = typer.Option(
        None,
        "--flip-hkjc-side/--no-flip-hkjc-side",
        help="HKJC-only side flip switch. CLI overrides FLIP_HKJC_SIDE when explicitly set.",
    ),
) -> None:
    """Send Telegram dry-run/live alerts from prediction CSV using configured thresholds."""
    try:
        typer.echo(
            send_telegram_alert(
                predictions_path=predictions_path,
                edge_threshold=edge_threshold,
                confidence_threshold=confidence_threshold,
                max_alerts=max_alerts,
                flip_hkjc_side=flip_hkjc_side,
            )
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)


@app.command("telegram-debug")
def telegram_debug(
    limit: int = typer.Option(10, help="Maximum Telegram updates to inspect."),
    send_test_message: bool = typer.Option(False, help="Send a test message to TELEGRAM_CHAT_ID after showing updates."),
) -> None:
    """Inspect Telegram bot updates and optionally send a PowerShell-friendly test message."""
    settings = get_settings()
    client = TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dry_run=settings.telegram_dry_run,
    )

    typer.echo(f"Telegram mode: {'dry-run' if settings.telegram_dry_run else 'live'}")
    typer.echo("PowerShell quick setup:")
    typer.echo('$env:TELEGRAM_BOT_TOKEN = "<your_bot_token>"')
    typer.echo('$env:TELEGRAM_CHAT_ID = "<your_chat_id>"')
    typer.echo('$env:TELEGRAM_DRY_RUN = "false"')

    try:
        records = client.get_updates(limit=limit)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    if not records:
        typer.echo("No Telegram updates found. Open the bot in Telegram and send /start or a test message first.")
        typer.echo("Then rerun: python -m src.main telegram-debug")
    else:
        typer.echo("Recent Telegram updates:")
        for index, record in enumerate(records, start=1):
            preview = record.text_preview or "<no text>"
            label = record.title_or_username or "<no title>"
            typer.echo(
                f"[{index}] chat_id={record.chat_id} type={record.chat_type} title={label} preview={preview}"
            )
        latest = records[-1]
        typer.echo(f"Suggested TELEGRAM_CHAT_ID: {latest.chat_id}")

    if send_test_message:
        validate_telegram_configuration(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=False,
            source="telegram-debug --send-test-message",
        )
        live_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=False,
        )
        try:
            message = live_client.send_message(
                text="Football-predictor Telegram test from telegram-debug",
                parse_mode="Markdown",
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1)
        typer.echo(f"Test message result: {message}")


@app.command("hkjc-debug-request")
def hkjc_debug_request(
    mode: str = typer.Option("handicap", help="Request mode: handicap, results, or results-detail."),
    from_har: Path | None = typer.Option(None, help="Path to a HAR export captured from HKJC pages."),
    from_curl: Path | None = typer.Option(None, help="Path to a copied DevTools cURL command text file."),
    from_bundle: Path | None = typer.Option(None, help="Path to a saved HKJC JS bundle."),
    from_html: Path | None = typer.Option(None, help="Path to a saved HKJC HTML shell."),
    output_path: Path | None = typer.Option(None, help="Optional output JSON path for the inspection report."),
    match_id: str | None = typer.Option(None, help="Optional match id override when replaying results-detail requests."),
    replay_live: bool = typer.Option(False, help="Replay the selected request candidate against the live HKJC endpoint."),
) -> None:
    """Inspect HKJC frontend request artifacts and optionally replay the recovered request."""
    report = inspect_request_sources(
        mode=mode,
        from_har=from_har,
        from_curl=from_curl,
        from_bundle=from_bundle,
        from_html=from_html,
    )
    resolved_output_path = output_path or report_path_for_mode(mode)
    write_inspection_report(report, resolved_output_path)

    typer.echo(json.dumps({
        "output_path": str(resolved_output_path),
        "selected_candidate": summarize_candidate(report.selected_candidate),
        "candidate_count": report.summary.get("candidate_count", 0),
        "notes": report.notes,
    }, ensure_ascii=False, indent=2))

    if replay_live and report.selected_candidate is not None:
        candidate = report.selected_candidate
        if mode.strip().lower() in {"results-detail", "results_detail", "detail", "result-detail"} and match_id:
            candidate = replace(
                candidate,
                variables={
                    **(candidate.variables or {}),
                    "matchId": match_id,
                },
            )
        replay = replay_request_candidate(candidate)
        replay_summary: dict[str, object] = {
            "status_code": replay.get("status_code"),
            "response_content_type": replay.get("response_content_type"),
            "row_count": replay.get("row_count"),
            "response_errors": replay.get("response_errors"),
        }
        if mode.strip().lower() in {"results", "results-detail", "results_detail", "detail", "result-detail"}:
            response_json = replay.get("response_json")
            data = response_json.get("data") if isinstance(response_json, dict) else None
            matches = data.get("matches") if isinstance(data, dict) else None
            if isinstance(matches, list):
                replay_summary["validated_results_preview"] = [
                    item.to_dict() for item in validate_results_snapshot(matches)[:3]
                ]
        typer.echo(json.dumps(replay_summary, ensure_ascii=False, indent=2))


@app.command("live-run-once")
def live_run_once(
    provider: str = typer.Option("hkjc", help="Live provider name (hkjc, mock, or csv)."),
    model_path: Path = typer.Option(Path("artifacts/model_bundle.pkl"), help="Trained model bundle path."),
    poll_timeout: int = typer.Option(15, help="Polling timeout seconds for provider fetch."),
    edge_threshold: float | None = typer.Option(None, help="Override edge threshold for live candidate filtering."),
    confidence_threshold: float | None = typer.Option(None, help="Override confidence threshold for live candidate filtering."),
    policy: str = typer.Option("fractional_kelly", help="Stake policy (flat,fixed_fraction,fractional_kelly,vol_target)."),
    flip_hkjc_side: bool | None = typer.Option(
        None,
        "--flip-hkjc-side/--no-flip-hkjc-side",
        help="HKJC-only side flip switch. CLI overrides FLIP_HKJC_SIDE when explicitly set.",
    ),
    max_alerts: int = typer.Option(3, help="Maximum alert candidates to process in one cycle."),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Dry-run by default; use --live for explicit opt-in."),
    output_dir: Path = typer.Option(Path("artifacts/live"), help="Phase 6 live artifact output directory."),
    run_id: str | None = typer.Option(None, help="Optional run identifier; writes to artifacts/live/<run-id>."),
    force: bool = typer.Option(False, help="Overwrite existing Phase 6 snapshot/status/dashboard files."),
) -> None:
    """Run one Phase 6 live cycle: ingest, normalize, predict, filter, and alert."""
    settings = get_settings()
    resolved_output_dir = _resolve_output_dir(output_dir, run_id)
    managed_files = [
        "live_snapshot.csv",
        "live_odds_history.csv",
        "live_model_outputs.csv",
        "live_candidates.csv",
        "live_alert_log.csv",
        "live_event_log.csv",
        "live_alert_preview.txt",
        "live_status.json",
        "dashboard.html",
    ]

    _guard_output_dir_files(
        output_dir=resolved_output_dir,
        file_names=managed_files,
        force=force,
        label="Phase 6 live-run-once",
    )
    if force:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        _reset_output_dir_files(resolved_output_dir, managed_files)

    try:
        client = build_market_feed_client(provider)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not dry_run:
        try:
            validate_telegram_configuration(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                dry_run=False,
                source="live-run-once",
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1)
    config = LiveRunnerConfig(
        provider=provider,
        model_path=model_path,
        poll_timeout_seconds=poll_timeout,
        edge_threshold=settings.min_edge_threshold if edge_threshold is None else edge_threshold,
        confidence_threshold=settings.min_confidence_threshold if confidence_threshold is None else confidence_threshold,
        policy=policy,
        flip_hkjc_side=settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side,
        max_alerts=max_alerts,
        dry_run=dry_run,
        output_dir=resolved_output_dir,
        run_id=run_id,
    )
    runner = LiveRunner(feed_client=client, config=config)
    try:
        summary = runner.run_once()
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)

    typer.echo(
        "\n".join(
            [
                "Phase 6 live-run-once completed.",
                f"mode={summary.mode} provider={summary.provider} run_id={summary.run_id}",
                f"output_dir={summary.output_dir}",
                f"snapshot_rows={summary.snapshot_rows} candidate_rows={summary.candidate_rows} alerts_sent={summary.alerts_sent}",
                f"raw_snapshot={summary.raw_snapshot_path}",
                f"odds_history={summary.odds_history_path}",
                f"alert_preview={summary.alert_preview_path}" if summary.alert_preview_path else "alert_preview=None",
                f"last_success_time_utc={summary.last_success_time_utc}",
            ]
        )
    )


@app.command("live-loop")
def live_loop(
    provider: str = typer.Option("hkjc", help="Live provider name (hkjc, mock, or csv)."),
    model_path: Path = typer.Option(Path("artifacts/model_bundle.pkl"), help="Trained model bundle path."),
    interval_seconds: int = typer.Option(60, help="Polling interval seconds between cycles."),
    poll_timeout: int = typer.Option(15, help="Polling timeout seconds for provider fetch."),
    edge_threshold: float | None = typer.Option(None, help="Override edge threshold for live candidate filtering."),
    confidence_threshold: float | None = typer.Option(None, help="Override confidence threshold for live candidate filtering."),
    policy: str = typer.Option("fractional_kelly", help="Stake policy (flat,fixed_fraction,fractional_kelly,vol_target)."),
    flip_hkjc_side: bool | None = typer.Option(
        None,
        "--flip-hkjc-side/--no-flip-hkjc-side",
        help="HKJC-only side flip switch. CLI overrides FLIP_HKJC_SIDE when explicitly set.",
    ),
    max_alerts: int = typer.Option(3, help="Maximum alert candidates to process per cycle."),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Dry-run by default; use --live for explicit opt-in."),
    output_dir: Path = typer.Option(Path("artifacts/live"), help="Phase 6 live artifact output directory."),
    run_id: str | None = typer.Option(None, help="Optional run identifier; writes to artifacts/live/<run-id>."),
    max_cycles: int | None = typer.Option(None, help="Optional cap on loop cycles (useful for tests)."),
    force: bool = typer.Option(False, help="Overwrite existing Phase 6 snapshot/status/dashboard files before loop starts."),
) -> None:
    """Run repeated Phase 6 live cycles with safe error handling and clear cycle logs."""
    settings = get_settings()
    resolved_output_dir = _resolve_output_dir(output_dir, run_id)
    managed_files = [
        "live_snapshot.csv",
        "live_odds_history.csv",
        "live_model_outputs.csv",
        "live_candidates.csv",
        "live_alert_log.csv",
        "live_event_log.csv",
        "live_alert_preview.txt",
        "live_status.json",
        "dashboard.html",
    ]

    _guard_output_dir_files(
        output_dir=resolved_output_dir,
        file_names=managed_files,
        force=force,
        label="Phase 6 live-loop",
    )
    if force:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        _reset_output_dir_files(resolved_output_dir, managed_files)

    try:
        client = build_market_feed_client(provider)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not dry_run:
        try:
            validate_telegram_configuration(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                dry_run=False,
                source="live-loop",
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1)
    config = LiveRunnerConfig(
        provider=provider,
        model_path=model_path,
        poll_timeout_seconds=poll_timeout,
        edge_threshold=settings.min_edge_threshold if edge_threshold is None else edge_threshold,
        confidence_threshold=settings.min_confidence_threshold if confidence_threshold is None else confidence_threshold,
        policy=policy,
        flip_hkjc_side=settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side,
        max_alerts=max_alerts,
        dry_run=dry_run,
        output_dir=resolved_output_dir,
        interval_seconds=interval_seconds,
        max_cycles=max_cycles,
        run_id=run_id,
        continue_on_error=True,
    )
    runner = LiveRunner(feed_client=client, config=config)

    try:
        summaries = runner.run_loop()
    except KeyboardInterrupt:
        typer.echo("Phase 6 live-loop interrupted by user (Ctrl+C).")
        raise typer.Exit(code=0)

    completed = len(summaries)
    latest = summaries[-1] if summaries else None
    typer.echo(f"Phase 6 live-loop completed: cycles={completed}")
    if latest is not None:
        typer.echo(
            f"latest cycle: mode={latest.mode} provider={latest.provider} output_dir={latest.output_dir} "
            f"snapshot_rows={latest.snapshot_rows} candidate_rows={latest.candidate_rows} alerts_sent={latest.alerts_sent}"
        )


@app.command("validate-results")
def validate_results(
    start_date: str = typer.Option(..., help="Validation start date (YYYY-MM-DD or YYYYMMDD)."),
    end_date: str = typer.Option(..., help="Validation end date (YYYY-MM-DD or YYYYMMDD)."),
    output_path: Path = typer.Option(Path("artifacts/live/results_validation.csv"), help="Validation CSV output path."),
    detail_curl_path: Path = typer.Option(
        Path("artifacts/debug/hkjc_results_detail_curl.txt"),
        help="Reference cURL file used to resolve matchResultDetails fbOddsTypes.",
    ),
    force: bool = typer.Option(False, help="Overwrite output CSV if it exists."),
) -> None:
    """Validate HKJC full-time handicap settlement consistency against internal engine."""
    _guard_output_file(output_path, force=force, label="Results validation output")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider = HKJCFootballProvider()
    detail_fb_odds_types = resolve_results_detail_fb_odds_types(detail_curl_path)
    frame = build_results_validation_report(
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        detail_fb_odds_types=detail_fb_odds_types,
    )
    frame.to_csv(output_path, index=False)

    total_rows = len(frame)
    matched_rows = int(frame["is_match"].sum()) if "is_match" in frame.columns and not frame.empty else 0
    mismatch_rows = total_rows - matched_rows
    typer.echo(
        "\n".join(
            [
                "HKJC validate-results completed.",
                f"date_range={start_date}..{end_date}",
                f"detail_fb_odds_types={','.join(detail_fb_odds_types)}",
                f"output_path={output_path}",
                f"rows={total_rows} matched={matched_rows} mismatched={mismatch_rows}",
            ]
        )
    )


@app.command("collect-hkjc-history")
def collect_hkjc_history_command(
    start_date: str = typer.Option(..., help="Collection start date (YYYY-MM-DD or YYYYMMDD)."),
    end_date: str = typer.Option(..., help="Collection end date (YYYY-MM-DD or YYYYMMDD)."),
    raw_output_dir: Path = typer.Option(
        Path("artifacts/hkjc_history/raw"),
        help="Directory for HKJC historical raw artifacts.",
    ),
    output_path: Path = typer.Option(
        Path("data/raw/hkjc/historical_matches_hkjc.csv"),
        help="Normalized HKJC historical output CSV path.",
    ),
    feature_output_path: Path = typer.Option(
        Path("data/processed/features_phase3_hkjc.csv"),
        help="Phase 3-compatible HKJC feature CSV output path.",
    ),
    build_features: bool = typer.Option(
        True,
        "--build-features/--no-build-features",
        help="Build Phase 3 feature CSV from normalized HKJC history.",
    ),
    detail_curl_path: Path = typer.Option(
        Path("artifacts/debug/hkjc_results_detail_curl.txt"),
        help="Reference cURL file used to resolve matchResultDetails fbOddsTypes.",
    ),
    timeout_seconds: int = typer.Option(20, help="Request timeout seconds for HKJC calls."),
    force: bool = typer.Option(False, help="Overwrite existing normalized/feature output CSV files."),
) -> None:
    """Collect HKJC historical results+odds and produce normalized plus Phase 3 feature CSV."""
    _guard_output_file(output_path, force=force, label="HKJC normalized output")
    if build_features:
        _guard_output_file(feature_output_path, force=force, label="HKJC feature output")

    detail_fb_odds_types = resolve_results_detail_fb_odds_types(detail_curl_path)
    summary = collect_hkjc_history(
        start_date=start_date,
        end_date=end_date,
        raw_output_dir=raw_output_dir,
        normalized_output_path=output_path,
        feature_output_path=feature_output_path if build_features else None,
        timeout_seconds=timeout_seconds,
        detail_fb_odds_types=detail_fb_odds_types,
    )

    typer.echo(
        "\n".join(
            [
                "HKJC history collection completed.",
                f"date_range={summary.start_date}..{summary.end_date}",
                f"raw_results={summary.raw_results_path}",
                f"raw_market={summary.raw_market_path}",
                f"raw_detail={summary.raw_detail_path}",
                f"normalized_output={summary.normalized_output_path}",
                f"feature_output={summary.feature_output_path}",
                f"result_matches={summary.result_matches}",
                f"market_rows={summary.market_rows}",
                f"normalized_rows={summary.normalized_rows}",
                f"feature_rows={summary.feature_rows}",
            ]
        )
    )


if __name__ == "__main__":
    app()
