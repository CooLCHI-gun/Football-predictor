from typing import Any

import pytest

from src.strategy.settlement import settle_handicap_bet


@pytest.mark.parametrize(
    "home_goals,away_goals,side,line,odds,stake,expected_outcome,expected_pnl,expected_roi",
    [
        (2, 1, "home", -0.5, 2.0, 100.0, "win", 100.0, 1.0),
        (2, 1, "away", +0.5, 1.9, 100.0, "lose", -100.0, -1.0),
        (2, 1, "home", -1.0, 1.95, 100.0, "push", 0.0, 0.0),
        (1, 1, "home", -0.25, 2.0, 100.0, "half-lose", -50.0, -0.5),
        (1, 1, "away", +0.25, 2.0, 100.0, "half-win", 50.0, 0.5),
        (2, 1, "home", -0.75, 2.0, 100.0, "half-win", 50.0, 0.5),
        (0, 0, "away", +0.75, 2.0, 100.0, "win", 100.0, 1.0),
    ],
)
def test_settle_handicap_outcomes(
    home_goals: int,
    away_goals: int,
    side: str,
    line: float,
    odds: float,
    stake: float,
    expected_outcome: str,
    expected_pnl: float,
    expected_roi: float,
) -> None:
    result = settle_handicap_bet(
        home_goals=home_goals,
        away_goals=away_goals,
        handicap_side=side,
        handicap_line=line,
        odds=odds,
        stake=stake,
    )

    assert result.outcome == expected_outcome
    assert result.stake == pytest.approx(stake)
    assert result.pnl == pytest.approx(expected_pnl)
    assert result.roi == pytest.approx(expected_roi)
    assert result.total_return == pytest.approx(stake + expected_pnl)


def test_quarter_line_components_for_minus_quarter() -> None:
    result = settle_handicap_bet(
        home_goals=1,
        away_goals=1,
        handicap_side="home",
        handicap_line=-0.25,
        odds=2.0,
        stake=100.0,
    )

    assert len(result.components) == 2
    component_lines = sorted(component.line for component in result.components)
    assert component_lines == [-0.5, 0.0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "home_goals": 1,
            "away_goals": 0,
            "handicap_side": "invalid",
            "handicap_line": -0.5,
            "odds": 2.0,
            "stake": 100.0,
        },
        {
            "home_goals": 1,
            "away_goals": 0,
            "handicap_side": "home",
            "handicap_line": -0.55,
            "odds": 2.0,
            "stake": 100.0,
        },
        {
            "home_goals": 1,
            "away_goals": 0,
            "handicap_side": "home",
            "handicap_line": -0.5,
            "odds": 1.0,
            "stake": 100.0,
        },
        {
            "home_goals": 1,
            "away_goals": 0,
            "handicap_side": "home",
            "handicap_line": -0.5,
            "odds": 2.0,
            "stake": 0.0,
        },
    ],
)
def test_invalid_settlement_inputs_raise_value_error(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        settle_handicap_bet(**kwargs)
