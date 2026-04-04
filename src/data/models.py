from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BetSide(str, Enum):
    HOME = "home"
    AWAY = "away"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_match_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_market: Mapped[str] = mapped_column(String(16), default="HKJC")
    competition: Mapped[str | None] = mapped_column(String(128), nullable=True)
    season: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kickoff_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)

    ft_home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ft_away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Full-time settlement scope is fixed: 90 minutes + injury time only.
    settlement_scope: Mapped[str] = mapped_column(String(32), default="FT_90_PLUS")


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    provider_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    bookmaker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_market: Mapped[str] = mapped_column(String(16), default="HKJC")


class HandicapLine(Base):
    __tablename__ = "handicap_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    odds_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("odds_snapshots.id"), nullable=True)

    line_value: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    is_closing_line: Mapped[bool] = mapped_column(default=False)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    predicted_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cover_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_bet: Mapped[bool] = mapped_column(default=False)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"), nullable=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)

    side: Mapped[str] = mapped_column(String(8), nullable=False)
    handicap_line: Mapped[float] = mapped_column(Float, nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)

    placed_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BetResult(Base):
    __tablename__ = "bet_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bet_id: Mapped[int] = mapped_column(ForeignKey("bets.id"), nullable=False, index=True)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    settled_time_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BankrollHistory(Base):
    __tablename__ = "bankroll_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    bankroll_before: Mapped[float] = mapped_column(Float, nullable=False)
    bankroll_after: Mapped[float] = mapped_column(Float, nullable=False)
    delta: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
