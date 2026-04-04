from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


StakePolicyName = Literal["flat", "fixed_fraction", "fractional_kelly", "vol_target"]


@dataclass(frozen=True)
class BankrollPolicyConfig:
    policy: StakePolicyName
    flat_stake: float
    fixed_fraction_pct: float
    fractional_kelly_factor: float
    vol_target_rolling_window_bets: int
    vol_target_target_per_bet_vol: float


@dataclass(frozen=True)
class RiskControlsConfig:
    max_stake_pct: float
    min_stake_amount: float
    daily_max_exposure_pct: float
    max_drawdown_pct: float
    daily_stop_loss_pct: float | None = None


@dataclass
class BankrollState:
    initial_bankroll: float
    current_bankroll: float
    peak_bankroll: float
    halted: bool = False
    daily_exposure: dict[str, float] = field(default_factory=dict)
    daily_pnl: dict[str, float] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        if self.peak_bankroll <= 0:
            return 0.0
        return max(0.0, (self.peak_bankroll - self.current_bankroll) / self.peak_bankroll)

    def register_settlement(self, day_key: str, stake: float, pnl: float) -> None:
        self.daily_exposure[day_key] = self.daily_exposure.get(day_key, 0.0) + stake
        self.daily_pnl[day_key] = self.daily_pnl.get(day_key, 0.0) + pnl
        self.current_bankroll += pnl
        self.peak_bankroll = max(self.peak_bankroll, self.current_bankroll)


@dataclass(frozen=True)
class StakeDecision:
    stake_amount: float
    projected_bankroll_after_stake: float
    raw_fraction: float
    applied_fraction: float
    policy: StakePolicyName
    reason: str
