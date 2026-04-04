from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, cast

import pandas as pd

from src.adapters.hkjc.interface import HKJCAdapter
from src.alerts.telegram import send_telegram_alert
from src.bankroll.controls import (
    allows_daily_exposure,
    allows_daily_stop_loss,
    apply_stake_bounds,
    should_halt_by_drawdown,
)
from src.bankroll.models import BankrollPolicyConfig, BankrollState, RiskControlsConfig
from src.bankroll.policies import compute_stake
from src.config.settings import get_settings
from src.config.strategy import load_bankroll_defaults, load_strategy_thresholds
from src.features.hk_market_compare import add_hk_vs_consensus_features
from src.live_feed.client import MarketFeedClient
from src.live_feed.models import NormalizedMarketSnapshot
from src.live_feed.repository import LiveFeedRepository
from src.models.baselines import generate_prediction_frame, load_model_bundle
from src.strategy.rules import maybe_flip_hkjc_side


@dataclass(frozen=True)
class LiveCycleResult:
    fetched_events: int
    normalized_snapshots: int
    inserted_snapshots: int
    skipped_snapshots: int
    predictions_path: Path
    candidates_path: Path
    alerts_message: str


class AlertSender(Protocol):
    def send_from_predictions(
        self,
        *,
        predictions_path: Path,
        edge_threshold: float,
        confidence_threshold: float,
        max_alerts: int,
        flip_hkjc_side: bool = False,
    ) -> str:
        ...


@dataclass(frozen=True)
class TelegramAlertSender(AlertSender):
    """Adapter that reuses existing CSV-based Telegram alert pipeline."""

    def send_from_predictions(
        self,
        *,
        predictions_path: Path,
        edge_threshold: float,
        confidence_threshold: float,
        max_alerts: int,
        flip_hkjc_side: bool,
    ) -> str:
        return send_telegram_alert(
            predictions_path=predictions_path,
            edge_threshold=edge_threshold,
            confidence_threshold=confidence_threshold,
            max_alerts=max_alerts,
            flip_hkjc_side=flip_hkjc_side,
        )


class LivePredictionService:
    """Ingest live feed, score matches, apply bankroll rules, and emit alerts."""

    def __init__(
        self,
        *,
        feed_client: MarketFeedClient,
        adapter: HKJCAdapter,
        repository: LiveFeedRepository,
        model_path: Path,
        output_dir: Path,
        lookahead_minutes: int = 24 * 60,
        max_alerts: int = 3,
        edge_threshold: float | None = None,
        confidence_threshold: float | None = None,
        bankroll_policy: BankrollPolicyConfig | None = None,
        risk_controls: RiskControlsConfig | None = None,
        bankroll_state: BankrollState | None = None,
        alert_sender: AlertSender | None = None,
        flip_hkjc_side: bool | None = None,
    ) -> None:
        settings = get_settings()
        thresholds = load_strategy_thresholds()
        bankroll_defaults = load_bankroll_defaults()

        self._feed_client = feed_client
        self._adapter = adapter
        self._repository = repository
        self._model_path = model_path
        self._output_dir = output_dir
        self._lookahead_minutes = lookahead_minutes
        self._max_alerts = max_alerts
        self._edge_threshold = thresholds.min_edge_threshold if edge_threshold is None else edge_threshold
        self._confidence_threshold = (
            thresholds.min_confidence_threshold if confidence_threshold is None else confidence_threshold
        )
        self._bankroll_policy = bankroll_policy or BankrollPolicyConfig(
            policy=_as_stake_policy_name(bankroll_defaults.bankroll_mode),
            flat_stake=thresholds.flat_stake,
            fixed_fraction_pct=bankroll_defaults.bankroll_fixed_fraction_pct,
            fractional_kelly_factor=bankroll_defaults.fractional_kelly_factor,
            vol_target_rolling_window_bets=bankroll_defaults.vol_target_rolling_window_bets,
            vol_target_target_per_bet_vol=bankroll_defaults.vol_target_target_per_bet_vol,
        )
        self._risk_controls = risk_controls or RiskControlsConfig(
            max_stake_pct=bankroll_defaults.max_stake_pct,
            min_stake_amount=bankroll_defaults.min_stake_amount,
            daily_max_exposure_pct=bankroll_defaults.daily_max_exposure_pct,
            max_drawdown_pct=bankroll_defaults.max_drawdown_pct,
            daily_stop_loss_pct=bankroll_defaults.daily_stop_loss_pct,
        )
        self._bankroll_state = bankroll_state or BankrollState(
            initial_bankroll=settings.bankroll_initial,
            current_bankroll=settings.bankroll_initial,
            peak_bankroll=settings.bankroll_initial,
        )
        self._alert_sender = alert_sender or TelegramAlertSender()
        self._flip_hkjc_side = settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side

    def run_cycle(self, *, as_of_utc: datetime | None = None) -> LiveCycleResult:
        run_time = as_of_utc or datetime.now(timezone.utc)
        events = self._feed_client.fetch_events(as_of_utc=run_time, lookahead_minutes=self._lookahead_minutes)
        normalized = self._adapter.normalize_batch(events)
        ingestion_result = self._repository.append_snapshots_idempotent(normalized)

        prediction_input = self._to_prediction_input(normalized)
        if prediction_input.empty:
            predictions_path, candidates_path = self._write_outputs(
                scored_df=pd.DataFrame(),
                candidate_df=pd.DataFrame(),
            )
            return LiveCycleResult(
                fetched_events=len(events),
                normalized_snapshots=len(normalized),
                inserted_snapshots=ingestion_result.inserted_rows,
                skipped_snapshots=ingestion_result.skipped_duplicates,
                predictions_path=predictions_path,
                candidates_path=candidates_path,
                alerts_message="No normalized upcoming markets found.",
            )

        model_bundle = load_model_bundle(self._model_path)
        scored_df = generate_prediction_frame(model_bundle, prediction_input)
        scored_df = self._add_edge_columns(scored_df)
        candidate_df = self._apply_strategy_and_risk(scored_df)
        predictions_path, candidates_path = self._write_outputs(scored_df=scored_df, candidate_df=candidate_df)

        alerts_message = "No candidate bets after strategy/risk filters."
        if not candidate_df.empty:
            alerts_message = self._send_alerts(predictions_path=candidates_path)

        return LiveCycleResult(
            fetched_events=len(events),
            normalized_snapshots=len(normalized),
            inserted_snapshots=ingestion_result.inserted_rows,
            skipped_snapshots=ingestion_result.skipped_duplicates,
            predictions_path=predictions_path,
            candidates_path=candidates_path,
            alerts_message=alerts_message,
        )

    def _send_alerts(self, *, predictions_path: Path) -> str:
        sender_signature = inspect.signature(self._alert_sender.send_from_predictions)
        sender_supports_flip = "flip_hkjc_side" in sender_signature.parameters
        if sender_supports_flip:
            return self._alert_sender.send_from_predictions(
                predictions_path=predictions_path,
                edge_threshold=self._edge_threshold,
                confidence_threshold=self._confidence_threshold,
                max_alerts=self._max_alerts,
                flip_hkjc_side=self._flip_hkjc_side,
            )

        return self._alert_sender.send_from_predictions(
            predictions_path=predictions_path,
            edge_threshold=self._edge_threshold,
            confidence_threshold=self._confidence_threshold,
            max_alerts=self._max_alerts,
        )

    def _to_prediction_input(self, snapshots: list[NormalizedMarketSnapshot]) -> pd.DataFrame:
        if not snapshots:
            return pd.DataFrame()

        rows: list[dict[str, object]] = []
        for snapshot in snapshots:
            implied_home_raw = 1.0 / snapshot.odds_home
            implied_away_raw = 1.0 / snapshot.odds_away
            implied_total = implied_home_raw + implied_away_raw
            implied_home = implied_home_raw / implied_total if implied_total > 0 else None
            implied_away = implied_away_raw / implied_total if implied_total > 0 else None

            rows.append(
                {
                    "provider_match_id": snapshot.provider_match_id,
                    "source_market": snapshot.source_market,
                    "competition": snapshot.competition,
                    "kickoff_time_utc": snapshot.kickoff_time_utc.isoformat(),
                    "home_team_name": snapshot.home_team_name,
                    "away_team_name": snapshot.away_team_name,
                    "injury_absence_index_home": snapshot.injury_absence_index_home,
                    "injury_absence_index_away": snapshot.injury_absence_index_away,
                    "squad_absence_score_home": snapshot.squad_absence_score_home,
                    "squad_absence_score_away": snapshot.squad_absence_score_away,
                    "handicap_open_line": snapshot.handicap_line,
                    "handicap_close_line": snapshot.handicap_line,
                    "odds_home_open": snapshot.odds_home,
                    "odds_away_open": snapshot.odds_away,
                    "odds_home_close": snapshot.odds_home,
                    "odds_away_close": snapshot.odds_away,
                    "implied_prob_home_open": implied_home,
                    "implied_prob_away_open": implied_away,
                    "implied_prob_home_close": implied_home,
                    "implied_prob_away_close": implied_away,
                    "missing_odds_flag": 0,
                    "missing_handicap_line_flag": 0,
                    "handicap_line_movement": 0.0,
                    "target_handicap_side": "home",
                    "hk_line": snapshot.handicap_line,
                    "consensus_line": None,
                    "hk_line_minus_consensus_line": None,
                    "hk_implied_prob": implied_home,
                    "hk_implied_prob_side": implied_home,
                    "consensus_implied_prob": None,
                    "consensus_implied_prob_side": None,
                    "hk_minus_consensus_prob": None,
                    "hk_off_market_flag": 0,
                    "hk_off_market_direction": 0.0,
                    "hk_off_market_agree_with_model_flag": 0,
                }
            )

        return add_hk_vs_consensus_features(pd.DataFrame(rows))

    def _add_edge_columns(self, scored_df: pd.DataFrame) -> pd.DataFrame:
        if scored_df.empty:
            return scored_df

        scored = scored_df.copy()
        scored["original_predicted_side"] = scored["predicted_side"].astype(str)
        scored["effective_predicted_side"] = scored.apply(
            lambda row: maybe_flip_hkjc_side(
                predicted_side=str(row.get("predicted_side", "")),
                source_market=str(row.get("source_market", "")),
                flip_hkjc_side=self._flip_hkjc_side,
            )
            or str(row.get("predicted_side", "")),
            axis=1,
        )
        scored["flip_hkjc_side_enabled"] = bool(self._flip_hkjc_side)
        scored["implied_probability"] = scored.apply(
            lambda row: row["implied_prob_home_close"] if str(row["effective_predicted_side"]) == "home" else row["implied_prob_away_close"],
            axis=1,
        )
        scored["edge"] = scored["model_probability"].astype(float) - scored["implied_probability"].astype(float)
        return scored

    def _apply_strategy_and_risk(self, scored_df: pd.DataFrame) -> pd.DataFrame:
        if scored_df.empty:
            return scored_df

        now_utc = pd.Timestamp.now(tz="UTC")
        kickoff_series = pd.to_datetime(scored_df["kickoff_time_utc"], utc=True, errors="coerce")
        filtered = scored_df[
            (kickoff_series >= now_utc)
            & (scored_df["edge"] >= self._edge_threshold)
            & (scored_df["confidence_score"] >= self._confidence_threshold)
        ].copy()

        if filtered.empty:
            return filtered

        filtered["stake_size"] = 0.0
        filtered["stake_reason"] = ""

        for index, row in filtered.sort_values("edge", ascending=False).iterrows():
            if should_halt_by_drawdown(state=self._bankroll_state, controls=self._risk_controls):
                break

            kickoff_day = str(pd.to_datetime(row["kickoff_time_utc"], utc=True).date())
            if not allows_daily_stop_loss(state=self._bankroll_state, day_key=kickoff_day, controls=self._risk_controls):
                continue

            odds = float(
                row["odds_home_close"] if str(row["effective_predicted_side"]) == "home" else row["odds_away_close"]
            )
            decision = compute_stake(
                current_bankroll=self._bankroll_state.current_bankroll,
                model_probability=float(row["model_probability"]),
                odds=odds,
                config=self._bankroll_policy,
            )
            bounded = apply_stake_bounds(
                decision=decision,
                current_bankroll=self._bankroll_state.current_bankroll,
                controls=self._risk_controls,
            )
            if bounded.stake_amount <= 0:
                continue
            if not allows_daily_exposure(
                state=self._bankroll_state,
                day_key=kickoff_day,
                stake=float(bounded.stake_amount),
                controls=self._risk_controls,
            ):
                continue

            filtered.at[index, "stake_size"] = float(bounded.stake_amount)
            filtered.at[index, "stake_reason"] = bounded.reason
            self._bankroll_state.daily_exposure[kickoff_day] = (
                self._bankroll_state.daily_exposure.get(kickoff_day, 0.0) + float(bounded.stake_amount)
            )

        filtered = filtered[filtered["stake_size"] > 0].copy()
        return filtered.sort_values("edge", ascending=False).head(self._max_alerts)

    def _write_outputs(self, *, scored_df: pd.DataFrame, candidate_df: pd.DataFrame) -> tuple[Path, Path]:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        predictions_path = self._output_dir / "live_predictions.csv"
        candidates_path = self._output_dir / "today_predictions.csv"
        scored_df.to_csv(predictions_path, index=False)
        candidate_df.to_csv(candidates_path, index=False)
        return predictions_path, candidates_path


def _as_stake_policy_name(raw_value: str) -> Literal["flat", "fixed_fraction", "fractional_kelly", "vol_target"]:
    value = raw_value.strip().lower()
    allowed = {
        "flat",
        "fixed_fraction",
        "fractional_kelly",
        "vol_target",
    }
    if value not in allowed:
        return "fractional_kelly"
    return cast(Literal["flat", "fixed_fraction", "fractional_kelly", "vol_target"], value)
