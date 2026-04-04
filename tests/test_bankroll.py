from src.bankroll.controls import apply_stake_bounds, should_halt_by_drawdown
from src.bankroll.models import BankrollPolicyConfig, BankrollState, RiskControlsConfig, StakeDecision, StakePolicyName
from src.bankroll.policies import compute_stake


def _base_policy_config(policy: StakePolicyName = "fractional_kelly") -> BankrollPolicyConfig:
    return BankrollPolicyConfig(
        policy=policy,
        flat_stake=100.0,
        fixed_fraction_pct=0.01,
        fractional_kelly_factor=0.25,
        vol_target_rolling_window_bets=5,
        vol_target_target_per_bet_vol=0.03,
    )


def test_fractional_kelly_returns_positive_stake_when_edge_positive() -> None:
    decision = compute_stake(
        current_bankroll=10000.0,
        model_probability=0.58,
        odds=2.0,
        config=_base_policy_config(policy="fractional_kelly"),
    )

    assert decision.stake_amount > 0.0
    assert decision.raw_fraction > 0.0
    assert decision.applied_fraction > 0.0


def test_apply_stake_bounds_caps_and_enforces_minimum() -> None:
    controls = RiskControlsConfig(
        max_stake_pct=0.02,
        min_stake_amount=10.0,
        daily_max_exposure_pct=0.05,
        max_drawdown_pct=0.25,
        daily_stop_loss_pct=None,
    )
    decision = StakeDecision(
        stake_amount=600.0,
        projected_bankroll_after_stake=9400.0,
        raw_fraction=0.06,
        applied_fraction=0.06,
        policy="fixed_fraction",
        reason="fixed_fraction",
    )

    bounded = apply_stake_bounds(decision=decision, current_bankroll=10000.0, controls=controls)
    assert bounded.stake_amount == 200.0
    assert "max_stake_cap" in bounded.reason


def test_drawdown_guard_halts_when_threshold_exceeded() -> None:
    controls = RiskControlsConfig(
        max_stake_pct=0.02,
        min_stake_amount=10.0,
        daily_max_exposure_pct=0.05,
        max_drawdown_pct=0.25,
        daily_stop_loss_pct=None,
    )
    state = BankrollState(initial_bankroll=10000.0, current_bankroll=7000.0, peak_bankroll=10000.0)
    assert should_halt_by_drawdown(state=state, controls=controls)
    assert state.halted is True


def test_vol_target_falls_back_when_history_is_too_short() -> None:
    decision = compute_stake(
        current_bankroll=10000.0,
        model_probability=0.58,
        odds=2.0,
        config=_base_policy_config(policy="vol_target"),
        recent_returns=[0.02, -0.01],
    )

    assert decision.stake_amount > 0.0
    assert decision.reason.startswith("vol_target_fallback")


def test_vol_target_scales_down_when_recent_vol_is_high() -> None:
    high_vol_returns = [0.2, -0.18, 0.15, -0.2, 0.22, -0.17]
    decision = compute_stake(
        current_bankroll=10000.0,
        model_probability=0.58,
        odds=2.0,
        config=_base_policy_config(policy="vol_target"),
        recent_returns=high_vol_returns,
    )

    assert decision.stake_amount > 0.0
    assert "vol_target_scaled_from" in decision.reason
