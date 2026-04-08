import pytest

from src.alerts.notifier import BetRecord, build_bet_alert_message, send_bet_alert
from src.alerts.telegram_client import TelegramClient, validate_telegram_configuration
from src.config.settings import get_settings


def test_telegram_client_dry_run_returns_message() -> None:
    client = TelegramClient(bot_token="", chat_id="", dry_run=True)
    response = client.send_message("test message")
    assert response.startswith("DRY_RUN:")


def test_send_bet_alert_uses_dry_run_client() -> None:
    client = TelegramClient(bot_token="", chat_id="", dry_run=True)
    bet = BetRecord(
        provider_match_id="x1",
        kickoff_time_utc="2026-01-01T12:00:00+00:00",
        home_team_name="A",
        away_team_name="B",
        handicap_line=-0.25,
        model_name="logistic_regression",
        model_approach="direct_cover",
        predicted_side="home",
        predicted_win_probability=0.57,
        implied_probability=0.50,
        edge=0.07,
        stake_size=100.0,
    )
    response = send_bet_alert(bet=bet, client=client)
    assert response.startswith("DRY_RUN:")
    assert "僅供研究參考" in response


def test_validate_telegram_configuration_has_actionable_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_telegram_configuration(bot_token="", chat_id="", dry_run=False, source="live-run-once")

    message = str(exc_info.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_CHAT_ID" in message
    assert "getUpdates" in message
    assert "Invoke-RestMethod" in message


def test_build_bet_alert_message_sanitizes_nan_team_fields() -> None:
    bet = BetRecord(
        provider_match_id="x2",
        kickoff_time_utc="2026-01-01T12:00:00+00:00",
        home_team_name="Manchester City",
        away_team_name="Liverpool",
        handicap_line=-0.25,
        model_name="xgboost",
        model_approach="direct_cover",
        predicted_side="home",
        predicted_win_probability=0.58,
        implied_probability=0.50,
        edge=0.08,
        stake_size=100.0,
        competition="English Premier League",
        competition_zh="nan",
        home_team_name_zh="nan",
        away_team_name_zh="null",
    )

    message = build_bet_alert_message(bet)

    assert "曼城 對 利物浦" in message
    assert "英超" in message
    assert "nan" not in message.lower()
    assert "null" not in message.lower()


def test_build_bet_alert_message_uses_traditional_chinese_debug_labels() -> None:
    bet = BetRecord(
        provider_match_id="x3",
        kickoff_time_utc="2026-01-01T12:00:00+00:00",
        home_team_name="A",
        away_team_name="B",
        handicap_line=0.0,
        model_name="xgboost",
        model_approach="direct_cover",
        predicted_side="away",
        original_predicted_side="home",
        predicted_win_probability=0.55,
        implied_probability=0.48,
        edge=0.07,
        stake_size=100.0,
        flip_hkjc_side_enabled=True,
    )

    message = build_bet_alert_message(bet)

    assert "模型方向" in message
    assert "生效方向" in message


def test_build_bet_alert_message_uses_expressive_tone_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALERT_TONE", raising=False)
    get_settings.cache_clear()

    bet = BetRecord(
        provider_match_id="x4",
        kickoff_time_utc="2026-01-01T12:00:00+00:00",
        home_team_name="A",
        away_team_name="B",
        handicap_line=-0.75,
        model_name="xgboost",
        model_approach="direct_cover",
        predicted_side="away",
        predicted_win_probability=0.75,
        implied_probability=0.49,
        edge=0.26,
        stake_size=100.0,
        confidence_score=0.51,
    )

    message = build_bet_alert_message(bet)

    assert "強勢訊號" in message
    assert "━━━━━━━━━━━━" in message
    get_settings.cache_clear()


def test_build_bet_alert_message_supports_neutral_tone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_TONE", "neutral")
    get_settings.cache_clear()

    bet = BetRecord(
        provider_match_id="x5",
        kickoff_time_utc="2026-01-01T12:00:00+00:00",
        home_team_name="A",
        away_team_name="B",
        handicap_line=-0.75,
        model_name="xgboost",
        model_approach="direct_cover",
        predicted_side="away",
        predicted_win_probability=0.75,
        implied_probability=0.49,
        edge=0.26,
        stake_size=100.0,
        confidence_score=0.51,
    )

    message = build_bet_alert_message(bet)

    assert "強勢訊號" not in message
    assert "━━━━━━━━━━━━" not in message
    assert "📊 模型觀點" in message
    get_settings.cache_clear()
