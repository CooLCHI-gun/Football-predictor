from typing import cast

import pandas as pd

from src.backtest.engine import simulate_bets
from src.backtest.metrics import estimate_risk_of_ruin, summarize_backtest
from src.bankroll.models import BankrollPolicyConfig, RiskControlsConfig
from src.bankroll.policies import compute_stake


def _policy_flat() -> BankrollPolicyConfig:
    return BankrollPolicyConfig(
        policy="flat",
        flat_stake=100.0,
        fixed_fraction_pct=0.01,
        fractional_kelly_factor=0.25,
        vol_target_rolling_window_bets=5,
        vol_target_target_per_bet_vol=0.03,
    )


def _controls() -> RiskControlsConfig:
    return RiskControlsConfig(
        max_stake_pct=0.1,
        min_stake_amount=1.0,
        daily_max_exposure_pct=1.0,
        max_drawdown_pct=0.99,
        daily_stop_loss_pct=None,
    )


def test_risk_of_ruin_increases_with_higher_stake_or_lower_win_probability() -> None:
    baseline = pd.DataFrame(
        {
            "model_probability": [0.6] * 20,
            "odds": [2.0] * 20,
            "stake": [100.0] * 20,
        }
    )
    higher_stake = baseline.copy()
    higher_stake["stake"] = 200.0

    lower_p = baseline.copy()
    lower_p["model_probability"] = 0.55

    ror_base = estimate_risk_of_ruin(baseline, initial_bankroll=10000.0)
    ror_higher_stake = estimate_risk_of_ruin(higher_stake, initial_bankroll=10000.0)
    ror_lower_p = estimate_risk_of_ruin(lower_p, initial_bankroll=10000.0)

    assert ror_base is not None
    assert ror_higher_stake is not None
    assert ror_lower_p is not None
    assert ror_higher_stake > ror_base
    assert ror_lower_p > ror_base


def test_vol_target_suggests_smaller_stake_under_higher_recent_volatility() -> None:
    config = BankrollPolicyConfig(
        policy="vol_target",
        flat_stake=100.0,
        fixed_fraction_pct=0.01,
        fractional_kelly_factor=0.25,
        vol_target_rolling_window_bets=5,
        vol_target_target_per_bet_vol=0.03,
    )
    low_vol_returns = [0.01, 0.012, 0.011, 0.009, 0.01, 0.011]
    high_vol_returns = [0.2, -0.18, 0.15, -0.2, 0.22, -0.17]

    low_vol_decision = compute_stake(
        current_bankroll=10000.0,
        model_probability=0.58,
        odds=2.0,
        config=config,
        recent_returns=low_vol_returns,
    )
    high_vol_decision = compute_stake(
        current_bankroll=10000.0,
        model_probability=0.58,
        odds=2.0,
        config=config,
        recent_returns=high_vol_returns,
    )

    assert high_vol_decision.stake_amount < low_vol_decision.stake_amount


def test_expected_value_formula_matches_toy_example() -> None:
    prediction_df = pd.DataFrame(
        [
            {
                "provider_match_id": "toy_1",
                "kickoff_time_utc": "2024-01-01 12:00:00+00:00",
                "competition": "E0",
                "source_market": "NON_HKJC",
                "model_name": "toy",
                "model_approach": "direct_cover",
                "predicted_side": "home",
                "model_probability": 0.6,
                "confidence_score": 0.6,
                "implied_prob_home_close": 0.5,
                "implied_prob_away_close": 0.5,
                "odds_home_close": 2.0,
                "odds_away_close": 2.0,
                "handicap_close_line": 0.0,
                "ft_home_goals": 1,
                "ft_away_goals": 0,
                "missing_odds_flag": 0,
            }
        ]
    )

    rows = simulate_bets(
        prediction_df=prediction_df,
        bankroll_initial=10000.0,
        min_edge_threshold=0.01,
        min_confidence_threshold=0.55,
        max_concurrent_bets=5,
        skip_missing_data=True,
        odds_source="closing",
        bankroll_policy=_policy_flat(),
        controls=_controls(),
    )
    assert len(rows) == 1
    row = rows[0]

    expected_ev = 0.6 * (2.0 - 1.0) * 100.0 - (1.0 - 0.6) * 100.0
    expected_value = cast(float, row["expected_value"])
    expected_roi = cast(float, row["expected_roi"])
    assert expected_value == expected_ev
    assert expected_roi == expected_ev / 100.0


def test_clv_proxy_present_with_closing_prob_and_na_when_missing() -> None:
    prediction_with_closing = pd.DataFrame(
        [
            {
                "provider_match_id": "clv_yes",
                "kickoff_time_utc": "2024-01-01 12:00:00+00:00",
                "predicted_side": "home",
                "model_probability": 0.6,
                "confidence_score": 0.6,
                "implied_prob_home_open": 0.47,
                "implied_prob_home_close": 0.52,
                "odds_home_open": 2.1,
                "odds_home_close": 1.92,
                "handicap_open_line": 0.0,
                "handicap_close_line": 0.0,
                "ft_home_goals": 1,
                "ft_away_goals": 0,
                "missing_odds_flag": 0,
            }
        ]
    )
    prediction_missing_closing = prediction_with_closing.copy()
    prediction_missing_closing["implied_prob_home_close"] = pd.NA

    rows_yes = simulate_bets(
        prediction_df=prediction_with_closing,
        bankroll_initial=10000.0,
        min_edge_threshold=0.01,
        min_confidence_threshold=0.55,
        max_concurrent_bets=5,
        skip_missing_data=True,
        odds_source="opening",
        bankroll_policy=_policy_flat(),
        controls=_controls(),
    )
    rows_na = simulate_bets(
        prediction_df=prediction_missing_closing,
        bankroll_initial=10000.0,
        min_edge_threshold=0.01,
        min_confidence_threshold=0.55,
        max_concurrent_bets=5,
        skip_missing_data=True,
        odds_source="opening",
        bankroll_policy=_policy_flat(),
        controls=_controls(),
    )

    assert len(rows_yes) == 1
    assert len(rows_na) == 1
    assert rows_yes[0]["clv_implied_edge"] is not None
    assert rows_na[0]["clv_implied_edge"] is None

    summary_yes = summarize_backtest(pd.DataFrame(rows_yes), predictions=pd.DataFrame(), initial_bankroll=10000.0)
    summary_na = summarize_backtest(pd.DataFrame(rows_na), predictions=pd.DataFrame(), initial_bankroll=10000.0)
    assert summary_yes["avg_clv_implied_edge"] is not None
    assert summary_na["avg_clv_implied_edge"] is None
