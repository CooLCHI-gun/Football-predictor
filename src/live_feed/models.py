from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExternalMarketEvent:
    """Raw upstream event payload fetched from a market provider."""

    provider_name: str
    payload: dict[str, object]


@dataclass(frozen=True)
class NormalizedMarketSnapshot:
    """Provider-agnostic market snapshot consumed by live prediction services."""

    provider_name: str
    provider_match_id: str
    source_market: str
    market_id: str
    competition: str
    competition_ch: str
    kickoff_time_utc: datetime
    snapshot_time_utc: datetime
    home_team_name: str
    home_team_name_ch: str
    away_team_name: str
    away_team_name_ch: str
    handicap_line: float
    odds_home: float
    odds_away: float
    injury_absence_index_home: float | None = None
    injury_absence_index_away: float | None = None
    squad_absence_score_home: float | None = None
    squad_absence_score_away: float | None = None

    def ingestion_key(self) -> str:
        """Stable idempotency key preventing duplicate ingestion writes."""
        snapshot_token = self.snapshot_time_utc.replace(microsecond=0).isoformat()
        return "|".join(
            [
                self.provider_name,
                self.provider_match_id,
                self.market_id,
                f"{self.handicap_line:.2f}",
                snapshot_token,
            ]
        )

    def to_row(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_match_id": self.provider_match_id,
            "source_market": self.source_market,
            "market_id": self.market_id,
            "competition": self.competition,
            "competition_ch": self.competition_ch,
            "kickoff_time_utc": self.kickoff_time_utc.isoformat(),
            "snapshot_time_utc": self.snapshot_time_utc.isoformat(),
            "home_team_name": self.home_team_name,
            "home_team_name_ch": self.home_team_name_ch,
            "away_team_name": self.away_team_name,
            "away_team_name_ch": self.away_team_name_ch,
            "handicap_line": self.handicap_line,
            "odds_home": self.odds_home,
            "odds_away": self.odds_away,
            "injury_absence_index_home": self.injury_absence_index_home,
            "injury_absence_index_away": self.injury_absence_index_away,
            "squad_absence_score_home": self.squad_absence_score_home,
            "squad_absence_score_away": self.squad_absence_score_away,
            "ingestion_key": self.ingestion_key(),
        }
