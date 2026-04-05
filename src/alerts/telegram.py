from __future__ import annotations

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

    # --- 去重: 排除已推送過的 provider_match_id ---
    alert_log_path = Path("artifacts/live/live_alert_log.csv")
    sent_ids = set()
    if alert_log_path.exists():
        try:
            log_df = pd.read_csv(alert_log_path)
            if "provider_match_id" in log_df.columns:
                sent_ids = set(log_df["provider_match_id"].astype(str))
        except Exception as e:
            LOGGER.warning(f"Failed to read alert log for deduplication: {e}")
    before_dedup = len(filtered)
    filtered = filtered[~filtered["provider_match_id"].astype(str).isin(sent_ids)]
    after_dedup = len(filtered)
    if before_dedup > after_dedup:
        LOGGER.info(f"Deduplication: {before_dedup-after_dedup} alerts skipped (already sent)")

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

    import csv
    from datetime import datetime, timezone

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

        # --- 新增: 每次推送後寫入 log ---
        log_row = {
            "cycle_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "alert_time_utc": datetime.now(timezone.utc).isoformat(),
            "provider_match_id": str(row.get("provider_match_id", "")),
            "alert_state": "sent",
            "message": lines[-1],
            "alert_message": lines[-1],
            "mode": "dry-run" if settings.telegram_dry_run else "live"
        }
        log_path = Path("artifacts/live/live_alert_log.csv")
        log_exists = log_path.exists()
        with open(log_path, "a", newline='', encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(log_row.keys()))
            if not log_exists:
                writer.writeheader()
            writer.writerow(log_row)

    LOGGER.info("Alert completed: sent=%s", sent)
    return "\n".join([f"Alerts processed: {sent}", *lines])


def _select_implied_probability(row: pd.Series, side: str, odds_source: str) -> float | None:
    suffix = "close" if odds_source == "closing" else "open"
    column = f"implied_prob_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)
