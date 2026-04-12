from __future__ import annotations

import csv
from datetime import datetime, timezone
import logging
from pathlib import Path

import pandas as pd

from src.alerts.notifier import BetRecord, send_bet_alert
from src.alerts.telegram_client import TelegramClient, validate_telegram_configuration
from src.config.settings import get_settings
from src.strategy.rules import maybe_flip_hkjc_side


LOGGER = logging.getLogger(__name__)


def send_telegram_alert(
    predictions_path: Path = Path("artifacts/predictions_full.csv"),
    edge_threshold: float | None = None,
    confidence_threshold: float | None = None,
    max_alerts: int = 3,
    flip_hkjc_side: bool | None = None,
) -> str:
    """Send Telegram research alerts from prediction CSV with threshold-based filtering."""
    settings = get_settings()
    if not predictions_path.exists():
        LOGGER.error("Alert aborted: predictions file not found at %s", predictions_path)
        return f"Predictions file not found: {predictions_path}"

    LOGGER.info("Alert loading predictions from %s", predictions_path)
    prediction_df = pd.read_csv(predictions_path)
    if prediction_df.empty:
        LOGGER.warning("Alert skipped: no predictions available")
        return "No predictions found."

    edge_threshold = settings.min_edge_threshold if edge_threshold is None else edge_threshold
    confidence_threshold = settings.min_confidence_threshold if confidence_threshold is None else confidence_threshold
    flip_hkjc_side = settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side

    prediction_df["kickoff_time_utc"] = pd.to_datetime(prediction_df["kickoff_time_utc"], utc=True)
    now_utc = pd.Timestamp.utcnow().tz_localize("UTC") if pd.Timestamp.utcnow().tzinfo is None else pd.Timestamp.utcnow()

    filtered = prediction_df[
        (prediction_df["confidence_score"] >= confidence_threshold)
        & (prediction_df["kickoff_time_utc"] >= now_utc)
    ].copy()

    filtered["original_predicted_side"] = filtered["predicted_side"].astype(str)
    filtered["effective_predicted_side"] = filtered.apply(
        lambda row: maybe_flip_hkjc_side(
            predicted_side=str(row.get("predicted_side", "")),
            source_market=str(row.get("source_market", "")),
            flip_hkjc_side=flip_hkjc_side,
        )
        or str(row.get("predicted_side", "")),
        axis=1,
    )
    filtered["flip_hkjc_side_enabled"] = bool(flip_hkjc_side)

    filtered["implied_probability"] = filtered.apply(
        lambda row: _select_implied_probability(
            row=row,
            side=str(row["effective_predicted_side"]),
            odds_source=settings.odds_source,
        ),
        axis=1,
    )

    filtered = filtered.dropna(subset=["implied_probability"])
    filtered["edge"] = filtered["model_probability"].astype(float) - filtered["implied_probability"].astype(float)
    filtered = filtered[filtered["edge"] >= edge_threshold]

    alert_log_path = Path("artifacts/live/live_alert_log.csv")
    filtered["match_key"] = filtered.apply(_build_match_key, axis=1)
    filtered["alert_day"] = filtered["kickoff_time_utc"].dt.date.astype(str)
    filtered["dedup_key"] = filtered["alert_day"] + "|" + filtered["match_key"]

    before_same_match_dedup = len(filtered)
    filtered = filtered.sort_values("edge", ascending=False).drop_duplicates(subset=["match_key"], keep="first")
    skipped_same_match = before_same_match_dedup - len(filtered)

    sent_keys = _read_sent_match_keys(alert_log_path)
    before_history_dedup = len(filtered)
    filtered = filtered[~filtered["dedup_key"].isin(sent_keys)]
    skipped_history = before_history_dedup - len(filtered)

    if skipped_same_match > 0 or skipped_history > 0:
        LOGGER.info(
            "Alert deduplication applied: skipped_same_match=%s skipped_history=%s",
            skipped_same_match,
            skipped_history,
        )

    if filtered.empty:
        LOGGER.info("Alert found no candidate bets after threshold filtering")
        return "No candidate bets meet edge/confidence criteria for upcoming matches."

    client = TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dry_run=settings.telegram_dry_run,
    )
    validate_telegram_configuration(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dry_run=settings.telegram_dry_run,
        source="alert command",
    )

    sent = 0
    lines: list[str] = []

    for _, row in filtered.sort_values("edge", ascending=False).head(max_alerts).iterrows():
        stake_size = float(settings.bankroll_initial * settings.bankroll_fixed_fraction_pct)
        bet = BetRecord(
            provider_match_id=str(row.get("provider_match_id", "")),
            kickoff_time_utc=str(row["kickoff_time_utc"]),
            home_team_name=str(row.get("home_team_name", "N/A")),
            away_team_name=str(row.get("away_team_name", "N/A")),
            handicap_line=float(row.get("handicap_close_line", 0.0)),
            model_name=str(row.get("model_name", settings.model_name)),
            model_approach=str(row.get("model_approach", settings.model_approach)),
            predicted_side=str(row["effective_predicted_side"]),
            original_predicted_side=str(row["original_predicted_side"]),
            predicted_win_probability=float(row["model_probability"]),
            implied_probability=float(row["implied_probability"]),
            edge=float(row["edge"]),
            stake_size=stake_size,
            flip_hkjc_side_enabled=bool(row.get("flip_hkjc_side_enabled", False)),
            competition=str(row.get("competition", "HKJC")),
            competition_zh=str(row.get("competition_ch", "")),
            home_team_name_zh=str(row.get("home_team_name_ch", "")),
            away_team_name_zh=str(row.get("away_team_name_ch", "")),
            market_id=str(row.get("market_id", "ah_ft")),
            match_number=str(row.get("match_number", "")),
            expected_value=float(row.get("expected_value", 0.0)),
        )
        result = send_bet_alert(bet=bet, client=client)
        sent += 1
        if settings.telegram_dry_run:
            lines.append(f"Alert {sent}: DRY_RUN prepared")
        else:
            lines.append(f"Alert {sent}: {result}")

        _append_alert_log_row(
            log_path=alert_log_path,
            row={
                "cycle_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "alert_time_utc": datetime.now(timezone.utc).isoformat(),
                "alert_day": str(row.get("alert_day", "")),
                "provider_match_id": str(row.get("provider_match_id", "")),
                "match_key": str(row.get("match_key", "")),
                "alert_state": "sent",
                "message": result,
                "alert_message": _extract_alert_message(result),
                "mode": "dry-run" if settings.telegram_dry_run else "live",
            },
        )

    LOGGER.info("Alert completed: sent=%s", sent)
    return "\n".join([f"Alerts processed: {sent}", *lines])


def _select_implied_probability(row: pd.Series, side: str, odds_source: str) -> float | None:
    suffix = "close" if odds_source == "closing" else "open"
    column = f"implied_prob_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _clean_token(value: object) -> str:
    text = str(value or "").strip()
    return " ".join(text.split()).lower()


def _build_match_key(row: pd.Series) -> str:
    provider_match_id = _clean_token(row.get("provider_match_id"))
    kickoff = _clean_token(row.get("kickoff_time_utc"))
    home = _clean_token(row.get("home_team_name"))
    away = _clean_token(row.get("away_team_name"))
    # Use composite key to reduce accidental collisions while keeping same-match dedup stable.
    return f"{provider_match_id}|{kickoff}|{home}|{away}"


def _read_sent_match_keys(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    try:
        log_df = pd.read_csv(log_path)
    except Exception as exc:
        LOGGER.warning("Failed to read alert log for deduplication: %s", exc)
        return set()

    if log_df.empty:
        return set()

    if "alert_day" in log_df.columns and "match_key" in log_df.columns:
        keys: set[str] = set()
        for day, key in zip(log_df["alert_day"].tolist(), log_df["match_key"].tolist()):
            day_token = str(day).strip()
            key_token = _clean_token(key)
            if day_token and day_token.lower() != "nan" and key_token:
                keys.add(f"{day_token}|{key_token}")
        return keys
    return set()


def _append_alert_log_row(log_path: Path, row: dict[str, str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    expected_fields = [
        "cycle_id",
        "alert_time_utc",
        "alert_day",
        "provider_match_id",
        "match_key",
        "alert_state",
        "message",
        "alert_message",
        "mode",
    ]
    row_payload = {field: row.get(field, "") for field in expected_fields}
    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=expected_fields)
        if write_header:
            writer.writeheader()
        writer.writerow(row_payload)


def _extract_alert_message(message: str) -> str:
    marker = "DRY_RUN: "
    if message.startswith(marker):
        return message[len(marker) :]
    return message
