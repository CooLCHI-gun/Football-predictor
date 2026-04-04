from __future__ import annotations

import pandas as pd

from src.backtest.engine import simulate_bets
from src.bankroll.models import BankrollPolicyConfig, RiskControlsConfig
from src.strategy.rules import maybe_flip_hkjc_side


def _base_policy() -> BankrollPolicyConfig:
    return BankrollPolicyConfig(
        policy="flat",
        flat_stake=100.0,
        fixed_fraction_pct=0.01,
        fractional_kelly_factor=0.2,
        vol_target_rolling_window_bets=10,
        vol_target_target_per_bet_vol=0.03,
    )


def _base_controls() -> RiskControlsConfig:
    return RiskControlsConfig(
        max_stake_pct=1.0,
        min_stake_amount=1.0,
        daily_max_exposure_pct=1.0,
        max_drawdown_pct=1.0,
        daily_stop_loss_pct=None,
    )


def test_maybe_flip_hkjc_side_only_flips_home_away_for_hkjc() -> None:
    assert maybe_flip_hkjc_side("home", "HKJC", True) == "away"
    assert maybe_flip_hkjc_side("away", "HKJC", True) == "home"
    assert maybe_flip_hkjc_side("home", "NON_HKJC", True) == "home"
    assert maybe_flip_hkjc_side("away", "CSV", True) == "away"
    assert maybe_flip_hkjc_side("home", "HKJC", False) == "home"
    assert maybe_flip_hkjc_side("draw", "HKJC", True) == "draw"
    assert maybe_flip_hkjc_side(None, "HKJC", True) is None


def test_simulate_bets_uses_effective_side_for_hkjc_only() -> None:
    prediction_df = pd.DataFrame(
        [
            {
                "provider_match_id": "m1",
                "kickoff_time_utc": "2026-04-01T12:00:00Z",
                "source_market": "HKJC",
                "predicted_side": "home",
                "model_probability": 0.55,
                "confidence_score": 0.70,
                "handicap_open_line": 0.0,
                "handicap_close_line": 0.0,
                "odds_home_open": 1.50,
                "odds_away_open": 2.40,
                "odds_home_close": 1.50,
                "odds_away_close": 2.40,
                "implied_prob_home_open": 0.45,
                "implied_prob_away_open": 0.40,
                "implied_prob_home_close": 0.45,
                "implied_prob_away_close": 0.40,
                "ft_home_goals": 2,
                "ft_away_goals": 1,
            },
            {
                "provider_match_id": "m2",
                "kickoff_time_utc": "2026-04-01T13:00:00Z",
                "source_market": "NON_HKJC",
                "predicted_side": "home",
                "model_probability": 0.55,
                "confidence_score": 0.70,
                "handicap_open_line": 0.0,
                "handicap_close_line": 0.0,
                "odds_home_open": 1.50,
                "odds_away_open": 2.40,
                "odds_home_close": 1.50,
                "odds_away_close": 2.40,
                "implied_prob_home_open": 0.45,
                "implied_prob_away_open": 0.40,
                "implied_prob_home_close": 0.45,
                "implied_prob_away_close": 0.40,
                "ft_home_goals": 2,
                "ft_away_goals": 1,
            },
        ]
    )

    trade_rows = simulate_bets(
        prediction_df=prediction_df,
        bankroll_initial=1000.0,
        min_edge_threshold=0.0,
        min_confidence_threshold=0.0,
        max_concurrent_bets=5,
        skip_missing_data=True,
        flip_hkjc_side=True,
        odds_source="closing",
        bankroll_policy=_base_policy(),
        controls=_base_controls(),
    )

    assert len(trade_rows) == 2

    hkjc_row = trade_rows[0]
    non_hkjc_row = trade_rows[1]

    assert hkjc_row["source_market"] == "HKJC"
    assert hkjc_row["original_predicted_side"] == "home"
    assert hkjc_row["effective_predicted_side"] == "away"
    assert hkjc_row["side"] == "away"
    assert hkjc_row["odds"] == 2.40
    assert hkjc_row["flip_hkjc_side_enabled"] is True

    assert non_hkjc_row["source_market"] == "NON_HKJC"
    assert non_hkjc_row["original_predicted_side"] == "home"
    assert non_hkjc_row["effective_predicted_side"] == "home"
    assert non_hkjc_row["side"] == "home"
    assert non_hkjc_row["odds"] == 1.50
    assert non_hkjc_row["flip_hkjc_side_enabled"] is True
