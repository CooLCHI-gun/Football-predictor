from __future__ import annotations

import statistics

from src.bankroll.models import BankrollPolicyConfig, StakeDecision


def compute_stake(
    *,
    current_bankroll: float,
    model_probability: float,
    odds: float,
    config: BankrollPolicyConfig,
    recent_returns: list[float] | None = None,
) -> StakeDecision:
    """Compute stake size from bankroll policy (flat/fixed_fraction/fractional_kelly/vol_target)."""
    if current_bankroll <= 0:
        return StakeDecision(
            stake_amount=0.0,
            projected_bankroll_after_stake=0.0,
            raw_fraction=0.0,
            applied_fraction=0.0,
            policy=config.policy,
            reason="bankroll_non_positive",
        )

    if config.policy == "flat":
        stake = max(0.0, config.flat_stake)
        fraction = stake / current_bankroll
        return StakeDecision(
            stake_amount=stake,
            projected_bankroll_after_stake=max(0.0, current_bankroll - stake),
            raw_fraction=fraction,
            applied_fraction=fraction,
            policy=config.policy,
            reason="flat_stake",
        )

    if config.policy == "fixed_fraction":
        fraction = max(0.0, config.fixed_fraction_pct)
        stake = current_bankroll * fraction
        return StakeDecision(
            stake_amount=stake,
            projected_bankroll_after_stake=max(0.0, current_bankroll - stake),
            raw_fraction=fraction,
            applied_fraction=fraction,
            policy=config.policy,
            reason="fixed_fraction",
        )

    if config.policy == "vol_target":
        fallback_decision = _conservative_fallback_decision(
            current_bankroll=current_bankroll,
            model_probability=model_probability,
            odds=odds,
            config=config,
        )
        window = max(2, int(config.vol_target_rolling_window_bets))
        target_vol = max(0.0, float(config.vol_target_target_per_bet_vol))
        returns = [float(item) for item in (recent_returns or [])]

        if len(returns) < window or target_vol <= 0.0:
            return StakeDecision(
                stake_amount=fallback_decision.stake_amount,
                projected_bankroll_after_stake=fallback_decision.projected_bankroll_after_stake,
                raw_fraction=fallback_decision.raw_fraction,
                applied_fraction=fallback_decision.applied_fraction,
                policy=config.policy,
                reason=f"vol_target_fallback:{fallback_decision.reason}",
            )

        recent_window = returns[-window:]
        estimated_vol = statistics.pstdev(recent_window)
        if estimated_vol <= 1e-8:
            return StakeDecision(
                stake_amount=fallback_decision.stake_amount,
                projected_bankroll_after_stake=fallback_decision.projected_bankroll_after_stake,
                raw_fraction=fallback_decision.raw_fraction,
                applied_fraction=fallback_decision.applied_fraction,
                policy=config.policy,
                reason=f"vol_target_fallback_low_vol:{fallback_decision.reason}",
            )

        scaling = target_vol / estimated_vol
        # Keep scaling conservative; max-stake and drawdown controls still apply globally.
        scaling = min(max(scaling, 0.25), 2.0)
        applied_fraction = max(0.0, fallback_decision.applied_fraction * scaling)
        stake = current_bankroll * applied_fraction
        return StakeDecision(
            stake_amount=stake,
            projected_bankroll_after_stake=max(0.0, current_bankroll - stake),
            raw_fraction=fallback_decision.raw_fraction,
            applied_fraction=applied_fraction,
            policy=config.policy,
            reason=f"vol_target_scaled_from:{fallback_decision.reason}",
        )

    kelly_fraction = _kelly_fraction(model_probability=model_probability, odds=odds)
    applied_fraction = max(0.0, kelly_fraction * max(0.0, config.fractional_kelly_factor))
    stake = current_bankroll * applied_fraction
    return StakeDecision(
        stake_amount=stake,
        projected_bankroll_after_stake=max(0.0, current_bankroll - stake),
        raw_fraction=kelly_fraction,
        applied_fraction=applied_fraction,
        policy=config.policy,
        reason="fractional_kelly",
    )


def _conservative_fallback_decision(
    *,
    current_bankroll: float,
    model_probability: float,
    odds: float,
    config: BankrollPolicyConfig,
) -> StakeDecision:
    """Return conservative fallback decision used by vol_target when volatility estimate is unreliable."""
    kelly_fraction = _kelly_fraction(model_probability=model_probability, odds=odds)
    kelly_applied_fraction = max(0.0, kelly_fraction * max(0.0, config.fractional_kelly_factor))
    kelly_stake = current_bankroll * kelly_applied_fraction

    if kelly_stake > 0:
        return StakeDecision(
            stake_amount=kelly_stake,
            projected_bankroll_after_stake=max(0.0, current_bankroll - kelly_stake),
            raw_fraction=kelly_fraction,
            applied_fraction=kelly_applied_fraction,
            policy=config.policy,
            reason="fractional_kelly_fallback",
        )

    flat_stake = max(0.0, config.flat_stake)
    flat_fraction = flat_stake / current_bankroll if current_bankroll > 0 else 0.0
    return StakeDecision(
        stake_amount=flat_stake,
        projected_bankroll_after_stake=max(0.0, current_bankroll - flat_stake),
        raw_fraction=flat_fraction,
        applied_fraction=flat_fraction,
        policy=config.policy,
        reason="flat_fallback",
    )


def _kelly_fraction(model_probability: float, odds: float) -> float:
    """
    Kelly criterion fraction for decimal odds.

    Let:
    - p = model win probability
    - q = 1 - p
    - b = odds - 1

    Full Kelly fraction:
    f* = (b * p - q) / b = (p * odds - 1) / (odds - 1)

    Interpretation:
    - f* <= 0: no positive edge, stake should be zero
    - f* > 0: suggested bankroll fraction under full Kelly

    Sports betting practice usually scales this down (e.g., 0.25x or 0.5x)
    because full Kelly can be very volatile and sensitive to model error.
    """
    p = min(max(float(model_probability), 0.0), 1.0)
    if odds <= 1.0:
        return 0.0

    b = float(odds) - 1.0
    q = 1.0 - p
    full_kelly = (b * p - q) / b
    return max(0.0, full_kelly)
