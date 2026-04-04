from datetime import datetime

from pydantic import BaseModel, Field


class MatchRow(BaseModel):
    provider_match_id: str = Field(min_length=1)
    source_market: str = Field(default="NON_HKJC")
    competition: str
    season: str
    kickoff_time_utc: datetime
    home_team_name: str
    away_team_name: str
    ft_home_goals: int | None = None
    ft_away_goals: int | None = None


class OddsSnapshotRow(BaseModel):
    provider_match_id: str = Field(min_length=1)
    snapshot_time_utc: datetime
    bookmaker: str | None = None
    source_market: str = Field(default="NON_HKJC")


class HandicapLineRow(BaseModel):
    provider_match_id: str = Field(min_length=1)
    snapshot_time_utc: datetime
    side: str
    line_value: float
    odds: float
    is_closing_line: bool = False
