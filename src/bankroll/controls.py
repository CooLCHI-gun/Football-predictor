from __future__ import annotations

from src.bankroll.models import BankrollState, RiskControlsConfig, StakeDecision


def should_halt_by_drawdown(state: BankrollState, controls: RiskControlsConfig) -> bool:
    """Return True once drawdown guard is breached; state remains halted afterwards."""
    if state.halted:
        return True
    if state.drawdown > controls.max_drawdown_pct:
        state.halted = True
        return True
    return False


def apply_stake_bounds(
    *,
    decision: StakeDecision,
    current_bankroll: float,
    controls: RiskControlsConfig,
) -> StakeDecision:
    """Apply max-stake and min-stake constraints to a raw stake decision."""
    if decision.stake_amount <= 0:
        return StakeDecision(
            stake_amount=0.0,
            projected_bankroll_after_stake=current_bankroll,
            raw_fraction=decision.raw_fraction,
            applied_fraction=0.0,
            policy=decision.policy,
            reason="non_positive_stake",
        )

    capped_stake = min(decision.stake_amount, current_bankroll * controls.max_stake_pct)
    if capped_stake < controls.min_stake_amount:
        return StakeDecision(
            stake_amount=0.0,
            projected_bankroll_after_stake=current_bankroll,
            raw_fraction=decision.raw_fraction,
            applied_fraction=0.0,
            policy=decision.policy,
            reason="below_min_stake",
        )

    applied_fraction = capped_stake / current_bankroll if current_bankroll > 0 else 0.0
    reason = decision.reason
    if capped_stake < decision.stake_amount:
        reason = f"{decision.reason}+max_stake_cap"

    return StakeDecision(
        stake_amount=capped_stake,
        projected_bankroll_after_stake=max(0.0, current_bankroll - capped_stake),
        raw_fraction=decision.raw_fraction,
        applied_fraction=applied_fraction,
        policy=decision.policy,
        reason=reason,
    )


def allows_daily_exposure(
    *,
    state: BankrollState,
    day_key: str,
    stake: float,
    controls: RiskControlsConfig,
) -> bool:
    """Check whether adding stake keeps day exposure within configured limit."""
    existing = state.daily_exposure.get(day_key, 0.0)
    limit = state.current_bankroll * controls.daily_max_exposure_pct
    return existing + stake <= limit


def allows_daily_stop_loss(
    *,
    state: BankrollState,
    day_key: str,
    controls: RiskControlsConfig,
) -> bool:
    """Check whether day-level stop loss permits another bet."""
    if controls.daily_stop_loss_pct is None:
        return True
    day_pnl = state.daily_pnl.get(day_key, 0.0)
    stop_loss_threshold = -1.0 * state.current_bankroll * controls.daily_stop_loss_pct
    return day_pnl > stop_loss_threshold
