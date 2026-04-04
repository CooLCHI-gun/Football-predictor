from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isclose


@dataclass(frozen=True)
class SettlementComponent:
    line: float
    stake: float
    outcome: str
    pnl: float
    total_return: float


@dataclass(frozen=True)
class SettlementResult:
    outcome: str
    pnl: float
    roi: float
    stake: float
    total_return: float
    components: list[SettlementComponent]

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "pnl": self.pnl,
            "roi": self.roi,
            "stake": self.stake,
            "total_return": self.total_return,
            "components": [asdict(component) for component in self.components],
        }


def settle_handicap_bet(
    home_goals: int,
    away_goals: int,
    handicap_side: str,
    handicap_line: float,
    odds: float,
    stake: float,
) -> SettlementResult:
    """Settle one full-time handicap bet (90 + injury time only).

    Quarter-ball lines are split into two half-stake components on adjacent lines.
    """
    side = handicap_side.lower().strip()
    if side not in {"home", "away"}:
        raise ValueError("handicap_side must be either 'home' or 'away'.")
    if odds <= 1.0:
        raise ValueError("odds must be > 1.0 for decimal odds format.")
    if stake <= 0:
        raise ValueError("stake must be > 0.")

    component_lines = _split_quarter_line(handicap_line)
    component_stake = stake / len(component_lines)

    components = [
        _settle_single_line(
            home_goals=home_goals,
            away_goals=away_goals,
            side=side,
            line=line,
            odds=odds,
            stake=component_stake,
        )
        for line in component_lines
    ]

    pnl = sum(component.pnl for component in components)
    total_return = sum(component.total_return for component in components)
    roi = pnl / stake
    outcome = _aggregate_outcome(components, stake=stake, odds=odds)

    return SettlementResult(
        outcome=outcome,
        pnl=pnl,
        roi=roi,
        stake=stake,
        total_return=total_return,
        components=components,
    )


def _split_quarter_line(line: float) -> list[float]:
    quarter_steps = round(line * 4)
    if not isclose(line * 4, quarter_steps, abs_tol=1e-9):
        raise ValueError("handicap_line must be in 0.25 increments.")

    remainder = abs(quarter_steps) % 4
    if remainder in {1, 3}:
        return [line - 0.25, line + 0.25]
    return [line]


def _settle_single_line(
    home_goals: int,
    away_goals: int,
    side: str,
    line: float,
    odds: float,
    stake: float,
) -> SettlementComponent:
    if side == "home":
        adjusted_margin = (home_goals - away_goals) + line
    else:
        adjusted_margin = (away_goals - home_goals) + line

    if adjusted_margin > 0:
        outcome = "win"
        total_return = stake * odds
    elif adjusted_margin < 0:
        outcome = "lose"
        total_return = 0.0
    else:
        outcome = "push"
        total_return = stake

    pnl = total_return - stake
    return SettlementComponent(
        line=line,
        stake=stake,
        outcome=outcome,
        pnl=pnl,
        total_return=total_return,
    )


def _aggregate_outcome(
    components: list[SettlementComponent],
    stake: float,
    odds: float,
) -> str:
    if len(components) == 1:
        return components[0].outcome

    total_return = sum(component.total_return for component in components)
    max_return = stake * odds
    min_return = 0.0

    if isclose(total_return, max_return, abs_tol=1e-9):
        return "win"
    if isclose(total_return, min_return, abs_tol=1e-9):
        return "lose"
    if isclose(total_return, stake, abs_tol=1e-9):
        return "push"
    if total_return > stake:
        return "half-win"
    return "half-lose"