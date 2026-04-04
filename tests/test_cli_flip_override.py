from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from src.config.settings import get_settings
from src.main import app


def test_backtest_cli_flip_flag_overrides_env(monkeypatch, tmp_path: Path) -> None:
    captured: list[bool | None] = []

    def _fake_run_backtest(*args, **kwargs):
        captured.append(kwargs.get("flip_hkjc_side"))
        return "ok"

    monkeypatch.setenv("FLIP_HKJC_SIDE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("src.main.run_backtest", _fake_run_backtest)

    runner = CliRunner()

    result_default = runner.invoke(
        app,
        [
            "backtest",
            "--input-csv-path",
            "data/processed/features_phase3.csv",
            "--output-dir",
            str(tmp_path / "bt-default"),
        ],
    )
    assert result_default.exit_code == 0

    result_explicit_off = runner.invoke(
        app,
        [
            "backtest",
            "--input-csv-path",
            "data/processed/features_phase3.csv",
            "--output-dir",
            str(tmp_path / "bt-off"),
            "--no-flip-hkjc-side",
        ],
    )
    assert result_explicit_off.exit_code == 0

    result_explicit_on = runner.invoke(
        app,
        [
            "backtest",
            "--input-csv-path",
            "data/processed/features_phase3.csv",
            "--output-dir",
            str(tmp_path / "bt-on"),
            "--flip-hkjc-side",
        ],
    )
    assert result_explicit_on.exit_code == 0

    # No CLI flag => leave as None and let runtime resolve from env/settings.
    assert captured[0] is None
    # Explicit CLI flags should override env default.
    assert captured[1] is False
    assert captured[2] is True

    get_settings.cache_clear()


def test_alert_cli_flip_flag_overrides_env(monkeypatch, tmp_path: Path) -> None:
    captured: list[bool | None] = []

    def _fake_send_telegram_alert(*args, **kwargs):
        captured.append(kwargs.get("flip_hkjc_side"))
        return "ok"

    prediction_path = tmp_path / "predictions.csv"
    prediction_path.write_text("provider_match_id\n", encoding="utf-8")

    monkeypatch.setenv("FLIP_HKJC_SIDE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("src.main.send_telegram_alert", _fake_send_telegram_alert)

    runner = CliRunner()

    result_default = runner.invoke(
        app,
        [
            "alert",
            "--predictions-path",
            str(prediction_path),
        ],
    )
    assert result_default.exit_code == 0

    result_explicit_off = runner.invoke(
        app,
        [
            "alert",
            "--predictions-path",
            str(prediction_path),
            "--no-flip-hkjc-side",
        ],
    )
    assert result_explicit_off.exit_code == 0

    result_explicit_on = runner.invoke(
        app,
        [
            "alert",
            "--predictions-path",
            str(prediction_path),
            "--flip-hkjc-side",
        ],
    )
    assert result_explicit_on.exit_code == 0

    assert captured[0] is None
    assert captured[1] is False
    assert captured[2] is True

    get_settings.cache_clear()


def test_live_run_once_cli_flip_flag_overrides_env(monkeypatch, tmp_path: Path) -> None:
    captured: list[bool] = []

    class _FakeRunner:
        def __init__(self, *, feed_client, config) -> None:
            captured.append(config.flip_hkjc_side)

        def run_once(self):
            return SimpleNamespace(
                mode="sandbox",
                provider="mock",
                run_id=None,
                output_dir=tmp_path / "live",
                snapshot_rows=0,
                candidate_rows=0,
                alerts_sent=0,
                raw_snapshot_path=tmp_path / "live" / "raw.json",
                odds_history_path=tmp_path / "live" / "odds.csv",
                alert_preview_path=tmp_path / "live" / "preview.txt",
                last_success_time_utc="2026-04-04T00:00:00+00:00",
            )

    monkeypatch.setenv("FLIP_HKJC_SIDE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("src.main.build_market_feed_client", lambda _provider: object())
    monkeypatch.setattr("src.main.LiveRunner", _FakeRunner)

    runner = CliRunner()

    result_default = runner.invoke(
        app,
        [
            "live-run-once",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "live-default"),
            "--force",
        ],
    )
    assert result_default.exit_code == 0

    result_explicit_off = runner.invoke(
        app,
        [
            "live-run-once",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--no-flip-hkjc-side",
            "--output-dir",
            str(tmp_path / "live-off"),
            "--force",
        ],
    )
    assert result_explicit_off.exit_code == 0

    result_explicit_on = runner.invoke(
        app,
        [
            "live-run-once",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--flip-hkjc-side",
            "--output-dir",
            str(tmp_path / "live-on"),
            "--force",
        ],
    )
    assert result_explicit_on.exit_code == 0

    # No CLI flag => use FLIP_HKJC_SIDE env default.
    assert captured[0] is True
    # Explicit CLI flags should override env default.
    assert captured[1] is False
    assert captured[2] is True

    get_settings.cache_clear()


def test_live_loop_cli_flip_flag_overrides_env(monkeypatch, tmp_path: Path) -> None:
    captured: list[bool] = []

    class _FakeRunner:
        def __init__(self, *, feed_client, config) -> None:
            captured.append(config.flip_hkjc_side)

        def run_loop(self):
            return []

    monkeypatch.setenv("FLIP_HKJC_SIDE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("src.main.build_market_feed_client", lambda _provider: object())
    monkeypatch.setattr("src.main.LiveRunner", _FakeRunner)

    runner = CliRunner()

    result_default = runner.invoke(
        app,
        [
            "live-loop",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--max-cycles",
            "1",
            "--interval-seconds",
            "1",
            "--output-dir",
            str(tmp_path / "loop-default"),
            "--force",
        ],
    )
    assert result_default.exit_code == 0

    result_explicit_off = runner.invoke(
        app,
        [
            "live-loop",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--no-flip-hkjc-side",
            "--max-cycles",
            "1",
            "--interval-seconds",
            "1",
            "--output-dir",
            str(tmp_path / "loop-off"),
            "--force",
        ],
    )
    assert result_explicit_off.exit_code == 0

    result_explicit_on = runner.invoke(
        app,
        [
            "live-loop",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--flip-hkjc-side",
            "--max-cycles",
            "1",
            "--interval-seconds",
            "1",
            "--output-dir",
            str(tmp_path / "loop-on"),
            "--force",
        ],
    )
    assert result_explicit_on.exit_code == 0

    # No CLI flag => use FLIP_HKJC_SIDE env default.
    assert captured[0] is True
    # Explicit CLI flags should override env default.
    assert captured[1] is False
    assert captured[2] is True

    get_settings.cache_clear()
