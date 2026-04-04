from dataclasses import dataclass

from src.config.settings import get_settings


@dataclass(frozen=True)
class StrategyThresholds:
    min_edge_threshold: float
    min_confidence_threshold: float
    max_concurrent_bets: int
    skip_missing_data: bool
    flat_stake: float


@dataclass(frozen=True)
class BankrollDefaults:
    bankroll_mode: str
    bankroll_initial: float
    bankroll_fixed_fraction_pct: float
    fractional_kelly_factor: float
    vol_target_rolling_window_bets: int
    vol_target_target_per_bet_vol: float
    max_stake_pct: float
    min_stake_amount: float
    daily_max_exposure_pct: float
    max_drawdown_pct: float
    daily_stop_loss_pct: float | None


def load_strategy_thresholds() -> StrategyThresholds:
    settings = get_settings()
    return StrategyThresholds(
        min_edge_threshold=settings.min_edge_threshold,
        min_confidence_threshold=settings.min_confidence_threshold,
        max_concurrent_bets=settings.max_concurrent_bets,
        skip_missing_data=settings.skip_missing_data,
        flat_stake=settings.flat_stake,
    )


def load_bankroll_defaults() -> BankrollDefaults:
    settings = get_settings()
    return BankrollDefaults(
        bankroll_mode=settings.bankroll_mode,
        bankroll_initial=settings.bankroll_initial,
        bankroll_fixed_fraction_pct=settings.bankroll_fixed_fraction_pct,
        fractional_kelly_factor=settings.fractional_kelly_factor,
        vol_target_rolling_window_bets=settings.vol_target_rolling_window_bets,
        vol_target_target_per_bet_vol=settings.vol_target_target_per_bet_vol,
        max_stake_pct=settings.bankroll_max_stake_pct or settings.kelly_cap,
        min_stake_amount=settings.bankroll_min_stake,
        daily_max_exposure_pct=settings.bankroll_daily_max_exposure_pct,
        max_drawdown_pct=settings.bankroll_max_drawdown_pct,
        daily_stop_loss_pct=settings.bankroll_daily_stop_loss_pct,
    )
