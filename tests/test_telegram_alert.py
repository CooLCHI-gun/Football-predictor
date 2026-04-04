import pytest

from src.alerts.notifier import BetRecord, send_bet_alert
from src.alerts.telegram_client import TelegramClient, validate_telegram_configuration


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
