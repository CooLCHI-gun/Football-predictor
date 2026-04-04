from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import pandas as pd

from src.bankroll.controls import allows_daily_exposure, allows_daily_stop_loss, apply_stake_bounds, should_halt_by_drawdown
from src.bankroll.models import BankrollPolicyConfig, BankrollState, RiskControlsConfig
from src.bankroll.policies import compute_stake
from src.config.settings import get_settings
from src.config.strategy import load_bankroll_defaults, load_strategy_thresholds
from src.features.hk_market_compare import add_hk_vs_consensus_features
from src.live_feed.models import NormalizedMarketSnapshot
from src.models.baselines import generate_prediction_frame, load_model_bundle
from src.strategy.rules import maybe_flip_hkjc_side


StakePolicy = Literal["flat", "fixed_fraction", "fractional_kelly", "vol_target"]


@dataclass(frozen=True)
class LivePredictionResult:
    snapshot_df: pd.DataFrame
    candidates_df: pd.DataFrame


class LivePredictionService:
    """Build live features, score model probabilities, and select alert candidates."""

    def __init__(
        self,
        *,
        model_path: Path,
        policy: StakePolicy,
        edge_threshold: float,
        confidence_threshold: float,
        max_alerts: int,
        historical_features_path: Path | None = None,
        flip_hkjc_side: bool | None = None,
    ) -> None:
        settings = get_settings()
        thresholds = load_strategy_thresholds()
        defaults = load_bankroll_defaults()
        self._model_path = model_path
        self._edge_threshold = edge_threshold
        self._confidence_threshold = confidence_threshold
        self._max_alerts = max_alerts
        self._historical_features_path = historical_features_path
        self._flip_hkjc_side = settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side
        self._bankroll_policy = BankrollPolicyConfig(
            policy=policy,
            flat_stake=thresholds.flat_stake,
            fixed_fraction_pct=defaults.bankroll_fixed_fraction_pct,
            fractional_kelly_factor=defaults.fractional_kelly_factor,
            vol_target_rolling_window_bets=defaults.vol_target_rolling_window_bets,
            vol_target_target_per_bet_vol=defaults.vol_target_target_per_bet_vol,
        )
        self._risk_controls = RiskControlsConfig(
            max_stake_pct=defaults.max_stake_pct,
            min_stake_amount=defaults.min_stake_amount,
            daily_max_exposure_pct=defaults.daily_max_exposure_pct,
            max_drawdown_pct=defaults.max_drawdown_pct,
            daily_stop_loss_pct=defaults.daily_stop_loss_pct,
        )

    def run(self, *, snapshots: list[NormalizedMarketSnapshot], bankroll_state: BankrollState) -> LivePredictionResult:
        if not snapshots:
            return LivePredictionResult(snapshot_df=pd.DataFrame(), candidates_df=pd.DataFrame())

        live_features = self._build_live_features(snapshots)
        model_bundle = load_model_bundle(self._model_path)
        predictions = generate_prediction_frame(model_bundle, live_features)
        snapshot_df = self._enrich_prediction_metrics(predictions)
        candidates_df = self._select_candidates(snapshot_df=snapshot_df, bankroll_state=bankroll_state)
        return LivePredictionResult(snapshot_df=snapshot_df, candidates_df=candidates_df)

    def _build_live_features(self, snapshots: list[NormalizedMarketSnapshot]) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        historical_lookup = self._load_historical_lookup()

        for snapshot in snapshots:
            implied_home_raw = 1.0 / snapshot.odds_home
            implied_away_raw = 1.0 / snapshot.odds_away
            implied_total = implied_home_raw + implied_away_raw
            implied_home = implied_home_raw / implied_total if implied_total > 0 else None
            implied_away = implied_away_raw / implied_total if implied_total > 0 else None

            home_hist = historical_lookup.get(snapshot.home_team_name, {})
            away_hist = historical_lookup.get(snapshot.away_team_name, {})

            rows.append(
                {
                    "provider_match_id": snapshot.provider_match_id,
                    "source_market": snapshot.source_market,
                    "market_id": snapshot.market_id,
                    "competition": snapshot.competition,
                    "competition_ch": snapshot.competition_ch,
                    "kickoff_time_utc": snapshot.kickoff_time_utc.isoformat(),
                    "home_team_name": snapshot.home_team_name,
                    "home_team_name_ch": snapshot.home_team_name_ch,
                    "away_team_name": snapshot.away_team_name,
                    "away_team_name_ch": snapshot.away_team_name_ch,
                    "injury_absence_index_home": snapshot.injury_absence_index_home,
                    "injury_absence_index_away": snapshot.injury_absence_index_away,
                    "squad_absence_score_home": snapshot.squad_absence_score_home,
                    "squad_absence_score_away": snapshot.squad_absence_score_away,
                    "handicap_open_line": snapshot.handicap_line,
                    "handicap_close_line": snapshot.handicap_line,
                    "handicap_line_movement": 0.0,
                    "missing_handicap_line_flag": 0,
                    "odds_home_open": snapshot.odds_home,
                    "odds_away_open": snapshot.odds_away,
                    "odds_home_close": snapshot.odds_home,
                    "odds_away_close": snapshot.odds_away,
                    "implied_prob_home_open": implied_home,
                    "implied_prob_away_open": implied_away,
                    "implied_prob_home_close": implied_home,
                    "implied_prob_away_close": implied_away,
                    "missing_odds_flag": 0,
                    "target_handicap_side": "home",
                    "home_form_points_last5": home_hist.get("home_form_points_last5", 0.0),
                    "home_form_points_last10": home_hist.get("home_form_points_last10", 0.0),
                    "away_form_points_last5": away_hist.get("away_form_points_last5", 0.0),
                    "away_form_points_last10": away_hist.get("away_form_points_last10", 0.0),
                    "home_recent_home_form_last5": home_hist.get("home_recent_home_form_last5", 0.0),
                    "away_recent_away_form_last5": away_hist.get("away_recent_away_form_last5", 0.0),
                    "home_goals_scored_last5": home_hist.get("home_goals_scored_last5", 0.0),
                    "home_goals_conceded_last5": home_hist.get("home_goals_conceded_last5", 0.0),
                    "home_goal_diff_last5": home_hist.get("home_goal_diff_last5", 0.0),
                    "away_goals_scored_last5": away_hist.get("away_goals_scored_last5", 0.0),
                    "away_goals_conceded_last5": away_hist.get("away_goals_conceded_last5", 0.0),
                    "away_goal_diff_last5": away_hist.get("away_goal_diff_last5", 0.0),
                    "rest_days_home": home_hist.get("rest_days_home", 0.0),
                    "rest_days_away": away_hist.get("rest_days_away", 0.0),
                    "rest_days_diff": 0.0,
                    "recent5_rest_days_diff": 0.0,
                    "recent5_hdc_cover_rate_home": 0.5,
                    "recent10_hdc_cover_rate_home": 0.5,
                    "recent5_hdc_cover_rate_away": 0.5,
                    "recent10_hdc_cover_rate_away": 0.5,
                    "recent5_goal_diff_mean_home": 0.0,
                    "recent10_goal_diff_mean_home": 0.0,
                    "recent5_goal_diff_mean_away": 0.0,
                    "recent10_goal_diff_mean_away": 0.0,
                    "recent5_xg_diff_mean_home": 0.0,
                    "recent5_xg_diff_mean_away": 0.0,
                    "recent10_xg_diff_mean_home": 0.0,
                    "recent10_xg_diff_mean_away": 0.0,
                    "recent_hdc_cover_ewm_alpha_0p3_home": 0.5,
                    "recent_hdc_cover_ewm_alpha_0p3_away": 0.5,
                    "recent5_hdc_cover_advantage": 0.0,
                    "recent10_hdc_cover_advantage": 0.0,
                    "h2h_last5_hdc_cover_rate": 0.5,
                    "h2h_last10_hdc_cover_rate": 0.5,
                    "h2h_home_last5_hdc_cover_rate": 0.5,
                    "h2h_last5_hdc_cover_mean": 0.0,
                    "h2h_last10_hdc_cover_mean": 0.0,
                    "h2h_last5_goal_diff_mean": 0.0,
                    "h2h_last5_xg_diff_mean": 0.0,
                    "h2h_sample_size_last5": 0,
                    "h2h_sample_size_last10": 0,
                    "elo_home_pre": home_hist.get("elo_home_pre", 1500.0),
                    "elo_away_pre": away_hist.get("elo_away_pre", 1500.0),
                    "elo_diff_pre": home_hist.get("elo_home_pre", 1500.0) - away_hist.get("elo_away_pre", 1500.0),
                    "history_home_matches_count": home_hist.get("history_home_matches_count", 0),
                    "history_away_matches_count": away_hist.get("history_away_matches_count", 0),
                    "missing_home_history_flag": int(home_hist.get("history_home_matches_count", 0) == 0),
                    "missing_away_history_flag": int(away_hist.get("history_away_matches_count", 0) == 0),
                    "missing_home_ft_goals_flag": 1,
                    "missing_away_ft_goals_flag": 1,
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

    def _enrich_prediction_metrics(self, prediction_df: pd.DataFrame) -> pd.DataFrame:
        if prediction_df.empty:
            return prediction_df

        enriched = prediction_df.copy()
        enriched["original_predicted_side"] = enriched["predicted_side"].astype(str)
        enriched["effective_predicted_side"] = enriched.apply(
            lambda row: maybe_flip_hkjc_side(
                predicted_side=str(row.get("predicted_side", "")),
                source_market=str(row.get("source_market", "")),
                flip_hkjc_side=self._flip_hkjc_side,
            )
            or str(row.get("predicted_side", "")),
            axis=1,
        )
        enriched["flip_hkjc_side_enabled"] = bool(self._flip_hkjc_side)
        enriched["implied_probability"] = enriched.apply(
            lambda row: row["implied_prob_home_close"] if str(row["effective_predicted_side"]) == "home" else row["implied_prob_away_close"],
            axis=1,
        )
        enriched["edge"] = enriched["model_probability"].astype(float) - enriched["implied_probability"].astype(float)
        enriched["expected_roi"] = (
            enriched["model_probability"].astype(float)
            * (
                enriched.apply(
                    lambda row: row["odds_home_close"] if str(row["effective_predicted_side"]) == "home" else row["odds_away_close"],
                    axis=1,
                ).astype(float)
                - 1.0
            )
            - (1.0 - enriched["model_probability"].astype(float))
        )
        return enriched

    def _select_candidates(self, *, snapshot_df: pd.DataFrame, bankroll_state: BankrollState) -> pd.DataFrame:
        if snapshot_df.empty:
            return snapshot_df

        filtered = snapshot_df[
            (snapshot_df["edge"].astype(float) >= self._edge_threshold)
            & (snapshot_df["confidence_score"].astype(float) >= self._confidence_threshold)
        ].copy()
        if filtered.empty:
            return filtered

        filtered["stake_size"] = 0.0
        filtered["stake_reason"] = ""
        filtered["suggested_policy"] = self._bankroll_policy.policy

        for index, row in filtered.sort_values("edge", ascending=False).iterrows():
            if should_halt_by_drawdown(state=bankroll_state, controls=self._risk_controls):
                break

            kickoff_day = str(pd.to_datetime(row["kickoff_time_utc"], utc=True).date())
            if not allows_daily_stop_loss(state=bankroll_state, day_key=kickoff_day, controls=self._risk_controls):
                continue

            odds = float(
                row["odds_home_close"] if str(row["effective_predicted_side"]) == "home" else row["odds_away_close"]
            )
            decision = compute_stake(
                current_bankroll=bankroll_state.current_bankroll,
                model_probability=float(row["model_probability"]),
                odds=odds,
                config=self._bankroll_policy,
            )
            bounded = apply_stake_bounds(
                decision=decision,
                current_bankroll=bankroll_state.current_bankroll,
                controls=self._risk_controls,
            )
            if bounded.stake_amount <= 0:
                continue
            if not allows_daily_exposure(
                state=bankroll_state,
                day_key=kickoff_day,
                stake=float(bounded.stake_amount),
                controls=self._risk_controls,
            ):
                continue

            filtered.at[index, "stake_size"] = float(bounded.stake_amount)
            filtered.at[index, "stake_reason"] = bounded.reason
            expected_roi_value = pd.to_numeric(filtered.at[index, "expected_roi"], errors="coerce")
            expected_roi = 0.0 if pd.isna(expected_roi_value) else float(expected_roi_value)
            filtered.at[index, "expected_value"] = expected_roi * float(bounded.stake_amount)
            bankroll_state.daily_exposure[kickoff_day] = bankroll_state.daily_exposure.get(kickoff_day, 0.0) + float(
                bounded.stake_amount
            )

        filtered = filtered[filtered["stake_size"] > 0].copy()
        return filtered.sort_values("edge", ascending=False).head(self._max_alerts)

    def _load_historical_lookup(self) -> dict[str, dict[str, float]]:
        if self._historical_features_path is None or not self._historical_features_path.exists():
            return {}

        frame = pd.read_csv(self._historical_features_path)
        if frame.empty or "home_team_name" not in frame.columns:
            return {}

        lookup: dict[str, dict[str, float]] = {}
        relevant_columns = [
            "home_form_points_last5",
            "home_form_points_last10",
            "home_recent_home_form_last5",
            "home_goals_scored_last5",
            "home_goals_conceded_last5",
            "home_goal_diff_last5",
            "rest_days_home",
            "elo_home_pre",
            "history_home_matches_count",
            "away_form_points_last5",
            "away_form_points_last10",
            "away_recent_away_form_last5",
            "away_goals_scored_last5",
            "away_goals_conceded_last5",
            "away_goal_diff_last5",
            "rest_days_away",
            "elo_away_pre",
            "history_away_matches_count",
        ]

        for _, row in frame.sort_values("kickoff_time_utc").iterrows():
            home = str(row.get("home_team_name", "")).strip()
            away = str(row.get("away_team_name", "")).strip()
            if home:
                lookup[home] = {column: float(row.get(column, 0.0) or 0.0) for column in relevant_columns if column in row}
            if away:
                lookup[away] = {column: float(row.get(column, 0.0) or 0.0) for column in relevant_columns if column in row}

        return lookup


def parse_policy(policy: str) -> StakePolicy:
    normalized = policy.strip().lower()
    allowed = {"flat", "fixed_fraction", "fractional_kelly", "vol_target"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported policy: {policy}. Supported policies: flat,fixed_fraction,fractional_kelly,vol_target")
    return cast(StakePolicy, normalized)
