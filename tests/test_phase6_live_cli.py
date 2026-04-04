from __future__ import annotations

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from src.config.settings import get_settings
from src.main import app


def _mock_model(monkeypatch) -> None:
    def _fake_load_model_bundle(model_path: Path) -> object:
        return object()

    def _fake_generate_prediction_frame(bundle: object, df: pd.DataFrame) -> pd.DataFrame:
        frame = df.copy()
        frame["predicted_side"] = "home"
        frame["model_probability"] = 0.63
        frame["confidence_score"] = 0.71
        frame["model_name"] = "logistic_regression"
        frame["model_approach"] = "direct_cover"
        return frame

    monkeypatch.setattr("src.live_feed.service.load_model_bundle", _fake_load_model_bundle)
    monkeypatch.setattr("src.live_feed.service.generate_prediction_frame", _fake_generate_prediction_frame)


def test_main_help_includes_phase6_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "live-run-once" in result.stdout
    assert "live-loop" in result.stdout


def test_live_run_once_creates_phase6_artifacts(tmp_path: Path, monkeypatch) -> None:
    _mock_model(monkeypatch)

    output_dir = tmp_path / "live"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "live-run-once",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--edge-threshold",
            "0.0",
            "--confidence-threshold",
            "0.0",
            "--max-alerts",
            "3",
            "--output-dir",
            str(output_dir),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "raw" / "latest_raw_mock.json").exists()
    assert (output_dir / "live_snapshot.csv").exists()
    assert (output_dir / "live_odds_history.csv").exists()
    assert (output_dir / "live_model_outputs.csv").exists()
    assert (output_dir / "live_candidates.csv").exists()
    assert (output_dir / "live_alert_log.csv").exists()
    assert (output_dir / "live_alert_preview.txt").exists()
    assert (output_dir / "live_status.json").exists()
    assert (output_dir / "dashboard.html").exists()

    alert_log = pd.read_csv(output_dir / "live_alert_log.csv")
    assert "alert_message" in alert_log.columns
    preview_text = (output_dir / "live_alert_preview.txt").read_text(encoding="utf-8")
    assert "⚽" in preview_text


def test_live_loop_runs_for_max_cycles(tmp_path: Path, monkeypatch) -> None:
    _mock_model(monkeypatch)

    output_dir = tmp_path / "live"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "live-loop",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--dry-run",
            "--edge-threshold",
            "0.0",
            "--confidence-threshold",
            "0.0",
            "--max-alerts",
            "2",
            "--interval-seconds",
            "1",
            "--max-cycles",
            "2",
            "--output-dir",
            str(output_dir),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "cycles=2" in result.stdout
    event_log = pd.read_csv(output_dir / "live_event_log.csv")
    assert (event_log["event"] == "cycle_summary").sum() >= 2


def test_live_run_once_live_mode_fails_early_when_telegram_missing(tmp_path: Path, monkeypatch) -> None:
    _mock_model(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    get_settings.cache_clear()

    output_dir = tmp_path / "live"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "live-run-once",
            "--provider",
            "mock",
            "--model-path",
            str(tmp_path / "dummy_model.pkl"),
            "--live",
            "--edge-threshold",
            "0.0",
            "--confidence-threshold",
            "0.0",
            "--max-alerts",
            "3",
            "--output-dir",
            str(output_dir),
            "--force",
        ],
    )

    get_settings.cache_clear()
    assert result.exit_code != 0
    assert "TELEGRAM_BOT_TOKEN" in result.stdout
    assert "getUpdates" in result.stdout


def test_telegram_debug_lists_recent_updates(monkeypatch) -> None:
    from src.alerts.telegram_client import TelegramUpdateRecord

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    get_settings.cache_clear()

    def _fake_get_updates(self, *, limit: int = 10):
        return [
            TelegramUpdateRecord(
                chat_id="257877292",
                chat_type="private",
                title_or_username="lccqs",
                text_preview="/start",
                update_id=1,
            )
        ]

    monkeypatch.setattr("src.alerts.telegram_client.TelegramClient.get_updates", _fake_get_updates)

    runner = CliRunner()
    result = runner.invoke(app, ["telegram-debug"])

    get_settings.cache_clear()
    assert result.exit_code == 0
    assert "Recent Telegram updates:" in result.stdout
    assert "257877292" in result.stdout
    assert "Suggested TELEGRAM_CHAT_ID" in result.stdout


def test_validate_results_cli_writes_report(tmp_path: Path, monkeypatch) -> None:
    output_path = tmp_path / "results_validation.csv"

    monkeypatch.setattr(
        "src.main.resolve_results_detail_fb_odds_types",
        lambda _path: ["HDC", "EDC"],
    )

    def _fake_fetch_results_snapshot(self, *, start_date: str, end_date: str, timeout: int = 20):
        return [
            {
                "id": "50065345",
                "homeTeam": {"name_en": "Team A"},
                "awayTeam": {"name_en": "Team B"},
                "tournament": {"name_en": "League X"},
                "results": [
                    {
                        "resultType": 1,
                        "homeResult": 1,
                        "awayResult": 0,
                        "stageId": 2,
                        "sequence": 1,
                        "payoutConfirmed": True,
                        "resultConfirmType": 1,
                    }
                ],
            }
        ]

    def _fake_fetch_result_detail_snapshot(self, *, match_id: str, timeout: int = 20, fb_odds_types=None):
        return {
            "id": match_id,
            "foPools": [
                {
                    "oddsType": "HDC",
                    "lines": [
                        {
                            "combinations": [
                                {"str": "H", "status": "WIN"},
                                {"str": "A", "status": "LOSE"},
                            ]
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(
        "src.live_feed.providers.hkjc_provider.HKJCFootballProvider.fetch_results_snapshot",
        _fake_fetch_results_snapshot,
    )
    monkeypatch.setattr(
        "src.live_feed.providers.hkjc_provider.HKJCFootballProvider.fetch_result_detail_snapshot",
        _fake_fetch_result_detail_snapshot,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate-results",
            "--start-date",
            "2026-04-01",
            "--end-date",
            "2026-04-03",
            "--output-path",
            str(output_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    frame = pd.read_csv(output_path)
    assert "match_id" in frame.columns
    assert "HKJC_result" in frame.columns
    assert "internal_result" in frame.columns
