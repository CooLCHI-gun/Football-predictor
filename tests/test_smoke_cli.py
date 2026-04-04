import inspect
import json
from pathlib import Path

from typer.testing import CliRunner
from typer.models import OptionInfo

from src.features.pipeline import build_feature_pipeline
from src.main import _resolve_output_dir, app, backtest, daily_maintenance, optimize, predict, train


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
    backtest_default = inspect.signature(backtest).parameters["input_csv_path"].default

    assert isinstance(train_default, OptionInfo) and train_default.default == expected_default
    assert isinstance(predict_default, OptionInfo) and predict_default.default == expected_default
    assert isinstance(backtest_default, OptionInfo) and backtest_default.default == expected_default


def test_train_exposes_proxy_alert_cli_options() -> None:
    train_signature = inspect.signature(train)
    missing_rate_default = train_signature.parameters["proxy_alert_missing_rate_threshold"].default
    consecutive_runs_default = train_signature.parameters["proxy_alert_consecutive_runs"].default

    assert isinstance(missing_rate_default, OptionInfo)
    assert isinstance(consecutive_runs_default, OptionInfo)


def test_backtest_and_optimize_expose_run_id_option() -> None:
    backtest_run_id = inspect.signature(backtest).parameters["run_id"].default
    optimize_run_id = inspect.signature(optimize).parameters["run_id"].default

    assert isinstance(backtest_run_id, OptionInfo)
    assert isinstance(optimize_run_id, OptionInfo)


def test_daily_maintenance_exposes_run_id_options() -> None:
    backtest_run_id = inspect.signature(daily_maintenance).parameters["backtest_run_id"].default
    optimize_run_id = inspect.signature(daily_maintenance).parameters["optimize_run_id"].default

    assert isinstance(backtest_run_id, OptionInfo)
    assert isinstance(optimize_run_id, OptionInfo)


def test_daily_maintenance_skip_wait_runs_backtest_then_optimize(tmp_path: Path, monkeypatch) -> None:
    feature_path = tmp_path / "features.csv"
    build_feature_pipeline(input_path=Path("data/raw/sample_matches_phase3.csv"), output_path=feature_path)

    calls: list[str] = []

    def _fake_run_backtest(*args, **kwargs) -> str:
        calls.append("backtest")
        return "backtest ok"

    def _fake_optimize_strategy(*args, **kwargs) -> str:
        calls.append("optimize")
        return "optimize ok"

    monkeypatch.setattr("src.main.run_backtest", _fake_run_backtest)
    monkeypatch.setattr("src.main.optimize_strategy", _fake_optimize_strategy)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "daily-maintenance",
            "--backtest-time",
            "00:01",
            "--optimize-time",
            "00:02",
            "--skip-wait",
            "--backtest-input-path",
            str(feature_path),
            "--optimize-input-path",
            str(feature_path),
            "--backtest-output-dir",
            str(tmp_path / "backtest"),
            "--optimize-output-dir",
            str(tmp_path / "optimizer"),
        ],
    )

    assert result.exit_code == 0
    assert calls == ["backtest", "optimize"]


def test_daily_maintenance_rejects_invalid_time_format() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "daily-maintenance",
            "--backtest-time",
            "25:99",
            "--optimize-time",
            "03:30",
            "--skip-wait",
        ],
    )

    assert result.exit_code != 0
    assert result.exception is not None


def test_railway_start_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["railway-start", "--help"])
    assert result.exit_code == 0


def test_railway_start_spawns_daily_and_live_commands(monkeypatch) -> None:
    launched: dict[str, list[str]] = {}

    class _FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout: int | None = None):
            return None

    class _FakeRunResult:
        returncode = 0

    def _fake_popen(cmd):
        launched["daily"] = cmd
        return _FakeProcess()

    def _fake_run(cmd, check=False):
        launched["live"] = cmd
        return _FakeRunResult()

    monkeypatch.setattr("src.main.subprocess.Popen", _fake_popen)
    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["railway-start", "--live-mode", "dry"])

    assert result.exit_code == 0
    assert "daily-maintenance" in launched["daily"]
    assert "live-loop" in launched["live"]


def test_railway_job_once_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["railway-job-once", "--help"])
    assert result.exit_code == 0


def test_railway_job_once_runs_live_and_due_analysis(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--backtest-time",
            "00:00",
            "--optimize-time",
            "00:00",
            "--state-path",
            str(state_path),
            "--feature-path",
            "data/processed/features_phase3_full.csv",
            "--model-path",
            "artifacts/model_bundle.pkl",
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert any(" backtest " in f" {item} " for item in joined)
    assert any(" optimize " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)


def test_railway_job_once_skips_same_day_analysis(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    today = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Hong_Kong")).strftime("%Y%m%d")
    state_path.write_text(
        '{"last_backtest_date": "%s", "last_optimize_date": "%s"}' % (today, today),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--backtest-time",
            "00:00",
            "--optimize-time",
            "00:00",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert not any(" backtest " in f" {item} " for item in joined)
    assert not any(" optimize " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)


def test_railway_job_once_skips_due_analysis_when_feature_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    missing_feature_path = tmp_path / "missing_features.csv"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--backtest-time",
            "00:00",
            "--optimize-time",
            "00:00",
            "--state-path",
            str(state_path),
            "--feature-path",
            str(missing_feature_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert not any(" backtest " in f" {item} " for item in joined)
    assert not any(" optimize " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)
    assert "skip backtest/optimize: feature CSV not found" in result.output


def test_railway_job_once_runs_due_retrain_before_live(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    feature_path = tmp_path / "features.csv"
    feature_path.write_text("match_id,target_cover\n1,1\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--retrain-enabled",
            "--retrain-time",
            "00:00",
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
            "--feature-path",
            str(feature_path),
            "--retrain-input-path",
            str(feature_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert any(" train " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)
    assert joined.index(next(item for item in joined if " train " in f" {item} ")) < joined.index(
        next(item for item in joined if " live-run-once " in f" {item} ")
    )


def test_railway_job_once_skips_retrain_when_input_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    missing_feature_path = tmp_path / "missing_retrain_features.csv"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--retrain-enabled",
            "--retrain-time",
            "00:00",
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
            "--retrain-input-path",
            str(missing_feature_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert not any(" train " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)
    assert "skip retrain: feature CSV not found" in result.output


def test_railway_job_once_runs_due_data_update_and_feature_rebuild(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    normalized_path = tmp_path / "normalized.csv"
    normalized_path.write_text("match_id,home_team,away_team\n1,A,B\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--data-update-enabled",
            "--data-update-time",
            "00:00",
            "--feature-rebuild-enabled",
            "--feature-rebuild-time",
            "00:00",
            "--data-update-normalized-output-path",
            str(normalized_path),
            "--feature-rebuild-input-path",
            str(normalized_path),
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert any(" download-real-data " in f" {item} " for item in joined)
    assert any(" build-features-full " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)


def test_railway_job_once_skips_feature_rebuild_when_input_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    missing_normalized = tmp_path / "missing_normalized.csv"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--feature-rebuild-enabled",
            "--feature-rebuild-time",
            "00:00",
            "--feature-rebuild-input-path",
            str(missing_normalized),
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    assert not any(" build-features-full " in f" {item} " for item in joined)
    assert any(" live-run-once " in f" {item} " for item in joined)
    assert "skip feature-rebuild: normalized input not found" in result.output


def test_railway_job_once_switch_auto_apply_uses_hkjc_feature_path(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    hkjc_feature_path = tmp_path / "features_hkjc.csv"
    hkjc_feature_path.write_text("match_id,target_cover\n1,1\n", encoding="utf-8")
    hkjc_summary = tmp_path / "hkjc_summary.csv"
    hkjc_summary.write_text(
        "total_matches_evaluated,total_bets_placed,win_rate,roi,max_drawdown,source_markets\n"
        "520,130,0.53,0.02,0.1,HKJC\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--switch-enabled",
            "--switch-auto-apply",
            "--switch-required-consecutive-passes",
            "1",
            "--switch-hkjc-summary-path",
            str(hkjc_summary),
            "--switch-hkjc-feature-path",
            str(hkjc_feature_path),
            "--backtest-time",
            "00:00",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    backtest_cmd = next(item for item in joined if " backtest " in f" {item} ")
    assert str(hkjc_feature_path) in backtest_cmd


def test_railway_job_once_live_auto_tune_applies_optimizer_best_params(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    feature_path = tmp_path / "features.csv"
    feature_path.write_text("match_id,target_cover\n1,1\n", encoding="utf-8")

    today_key = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Hong_Kong")).strftime("%Y%m%d")
    optimizer_output_dir = tmp_path / "optimizer"
    best_params_path = optimizer_output_dir / f"daily_optimize_{today_key}" / "best_params.json"
    best_params_path.parent.mkdir(parents=True, exist_ok=True)
    best_params_path.write_text(
        '{"min_edge_threshold": 0.015, "min_confidence_threshold": 0.52, "max_alerts": 2}',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
            "--feature-path",
            str(feature_path),
            "--optimizer-output-dir",
            str(optimizer_output_dir),
            "--live-auto-tune-enabled",
        ],
    )

    assert result.exit_code == 0
    joined = [" ".join(cmd) for cmd in calls]
    live_cmd = next(item for item in joined if " live-run-once " in f" {item} ")
    assert "--edge-threshold 0.015" in live_cmd
    assert "--confidence-threshold 0.52" in live_cmd
    assert "--max-alerts 2" in live_cmd


def test_railway_job_once_writes_switch_decision_audit(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)

    state_path = tmp_path / "state.json"
    decision_path = tmp_path / "switch_decision.json"
    hkjc_summary = tmp_path / "hkjc_summary.csv"
    hkjc_summary.write_text(
        "total_matches_evaluated,total_bets_placed,win_rate,roi,max_drawdown,source_markets\n"
        "520,130,0.53,0.02,0.1,HKJC\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--switch-enabled",
            "--switch-required-consecutive-passes",
            "1",
            "--switch-hkjc-summary-path",
            str(hkjc_summary),
            "--switch-decision-output-path",
            str(decision_path),
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    assert decision_path.exists()
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    assert payload["evaluated"] is True
    assert payload["passed"] is True
    assert payload["switch_mode"] == "HKJC_ONLY"


def test_railway_job_once_sends_switch_telegram_report_when_enabled(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    sent_messages: list[str] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(list(args))
        return _FakeCompleted()

    class _FakeTelegramClient:
        def __init__(self, bot_token: str, chat_id: str, dry_run: bool = True) -> None:
            self.bot_token = bot_token
            self.chat_id = chat_id
            self.dry_run = dry_run

        def send_message(self, text: str, parse_mode: str = "Markdown") -> str:
            sent_messages.append(text)
            return "DRY_RUN"

    monkeypatch.setattr("src.main.subprocess.run", _fake_run)
    monkeypatch.setattr("src.main.TelegramClient", _FakeTelegramClient)
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    state_path = tmp_path / "state.json"
    hkjc_summary = tmp_path / "hkjc_summary.csv"
    hkjc_summary.write_text(
        "total_matches_evaluated,total_bets_placed,win_rate,roi,max_drawdown,source_markets\n"
        "300,40,0.49,0.0,0.2,NON_HKJC\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "railway-job-once",
            "--timezone-name",
            "Asia/Hong_Kong",
            "--switch-enabled",
            "--switch-telegram-report-enabled",
            "--switch-hkjc-summary-path",
            str(hkjc_summary),
            "--backtest-time",
            "23:59",
            "--optimize-time",
            "23:59",
            "--state-path",
            str(state_path),
        ],
    )

    assert result.exit_code == 0
    assert sent_messages
    assert "Switch decision report" in sent_messages[0]
    assert "FAIL" in sent_messages[0]


def test_resolve_output_dir_uses_run_id_subdirectory() -> None:
    assert _resolve_output_dir(Path("artifacts/backtest"), None) == Path("artifacts/backtest")
    assert _resolve_output_dir(Path("artifacts/backtest"), "20260403_pm1") == Path("artifacts/backtest/20260403_pm1")
