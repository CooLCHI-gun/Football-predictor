from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class InjurySignal:
    """Pre-match squad availability proxy features for both sides."""

    home_index: float | None
    away_index: float | None


class InjuryDataSource(Protocol):
    """External injury signal contract.

    Future adapters can call APIs, local DB, or cached files to return pre-match
    injury absence indices without changing feature pipeline logic.
    """

    def get_injury_signal(self, row: pd.Series) -> InjurySignal:
        ...


class RowBackedInjuryDataSource:
    """Fallback adapter that reads optional columns from current row.

    This keeps schema stable now while allowing later replacement with real
    external data sources.
    """

    def __init__(
        self,
        *,
        home_candidates: tuple[str, ...] = (
            "injury_absence_index_home",
            "squad_absence_score_home",
            "injury_index_home",
        ),
        away_candidates: tuple[str, ...] = (
            "injury_absence_index_away",
            "squad_absence_score_away",
            "injury_index_away",
        ),
    ) -> None:
        self._home_candidates = home_candidates
        self._away_candidates = away_candidates

    def get_injury_signal(self, row: pd.Series) -> InjurySignal:
        return InjurySignal(
            home_index=_read_first_float(row, self._home_candidates),
            away_index=_read_first_float(row, self._away_candidates),
        )


class NullInjuryDataSource:
    """No-op adapter used when no external injury signal is available."""

    def get_injury_signal(self, row: pd.Series) -> InjurySignal:
        _ = row
        return InjurySignal(home_index=None, away_index=None)


def default_injury_data_source() -> InjuryDataSource:
    return RowBackedInjuryDataSource()


def _read_first_float(row: pd.Series, candidates: tuple[str, ...]) -> float | None:
    for key in candidates:
        value = row.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed):
            continue
        return float(parsed)
    return None
