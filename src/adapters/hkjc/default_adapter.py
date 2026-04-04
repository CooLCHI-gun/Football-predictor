from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from src.adapters.hkjc.interface import HKJCAdapter
from src.live_feed.models import ExternalMarketEvent, NormalizedMarketSnapshot


@dataclass(frozen=True)
class HKJCAdapterConfig:
    """Configuration-driven field mapping for HKJC-oriented normalization."""

    source_market: str = "HKJC_LIKE"
    field_map: dict[str, str] = field(
        default_factory=lambda: {
            "provider_match_id": "provider_match_id",
            "market_id": "market_id",
            "competition": "competition",
            "competition_ch": "competition_ch",
            "kickoff_time_utc": "kickoff_time_utc",
            "snapshot_time_utc": "snapshot_time_utc",
            "home_team_name": "home_team_name",
            "home_team_name_ch": "home_team_name_ch",
            "away_team_name": "away_team_name",
            "away_team_name_ch": "away_team_name_ch",
            "handicap_line": "handicap_line",
            "odds_home": "odds_home",
            "odds_away": "odds_away",
            "side_semantic": "side_semantic",
        }
    )
    side_map: dict[str, str] = field(
        default_factory=lambda: {
            "home": "home",
            "away": "away",
            "h": "home",
            "a": "away",
            "主": "home",
            "客": "away",
        }
    )


class DefaultHKJCAdapter(HKJCAdapter):
    """Default HKJC-oriented adapter with notation normalization hooks."""

    def __init__(self, config: HKJCAdapterConfig | None = None) -> None:
        self._config = config or HKJCAdapterConfig()

    def normalize_batch(self, events: list[ExternalMarketEvent]) -> list[NormalizedMarketSnapshot]:
        normalized: list[NormalizedMarketSnapshot] = []
        for event in events:
            mapped = self.normalize_event(event)
            if mapped is not None:
                normalized.append(mapped)
        return normalized

    def normalize_event(self, event: ExternalMarketEvent) -> NormalizedMarketSnapshot | None:
        payload = event.payload
        provider_match_id = self._read_str(payload, "provider_match_id")
        market_id = self._read_str(payload, "market_id")
        competition = self._read_str(payload, "competition")
        competition_ch = self._read_optional_str(payload, "competition_ch")
        home_team_name = self._read_str(payload, "home_team_name")
        home_team_name_ch = self._read_optional_str(payload, "home_team_name_ch")
        away_team_name = self._read_str(payload, "away_team_name")
        away_team_name_ch = self._read_optional_str(payload, "away_team_name_ch")

        if not all([provider_match_id, market_id, competition, home_team_name, away_team_name]):
            return None

        kickoff_time_utc = self._read_datetime(payload, "kickoff_time_utc")
        snapshot_time_utc = self._read_datetime(payload, "snapshot_time_utc")
        if kickoff_time_utc is None or snapshot_time_utc is None:
            return None

        handicap_line = self._normalize_handicap_line(payload)
        odds_home, odds_away = self._normalize_odds(payload)
        if handicap_line is None or odds_home is None or odds_away is None:
            return None

        injury_home = self._first_optional_float(
            payload,
            ["injury_absence_index_home", "injury_index_home", "home_injury_index"],
        )
        injury_away = self._first_optional_float(
            payload,
            ["injury_absence_index_away", "injury_index_away", "away_injury_index"],
        )
        squad_home = self._first_optional_float(
            payload,
            ["squad_absence_score_home", "home_squad_absence_score"],
        )
        squad_away = self._first_optional_float(
            payload,
            ["squad_absence_score_away", "away_squad_absence_score"],
        )

        return NormalizedMarketSnapshot(
            provider_name=event.provider_name,
            provider_match_id=provider_match_id,
            source_market=self._config.source_market,
            market_id=market_id,
            competition=competition,
            competition_ch=competition_ch,
            kickoff_time_utc=kickoff_time_utc,
            snapshot_time_utc=snapshot_time_utc,
            home_team_name=home_team_name,
            home_team_name_ch=home_team_name_ch,
            away_team_name=away_team_name,
            away_team_name_ch=away_team_name_ch,
            handicap_line=handicap_line,
            odds_home=odds_home,
            odds_away=odds_away,
            injury_absence_index_home=injury_home,
            injury_absence_index_away=injury_away,
            squad_absence_score_home=squad_home,
            squad_absence_score_away=squad_away,
        )

    def _read_str(self, payload: dict[str, object], logical_key: str) -> str:
        source_key = self._config.field_map[logical_key]
        value = payload.get(source_key)
        if value is None:
            return ""
        return str(value).strip()

    def _read_datetime(self, payload: dict[str, object], logical_key: str) -> datetime | None:
        source_key = self._config.field_map[logical_key]
        raw_value = payload.get(source_key)
        if raw_value is None:
            return None
        parsed = pd.to_datetime(str(raw_value), utc=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()

    def _read_optional_str(self, payload: dict[str, object], logical_key: str) -> str:
        source_key = self._config.field_map.get(logical_key)
        if not source_key:
            return ""
        value = payload.get(source_key)
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_handicap_line(self, payload: dict[str, object]) -> float | None:
        source_key = self._config.field_map["handicap_line"]
        raw = payload.get(source_key)
        if raw is None:
            return None

        if isinstance(raw, (int, float)):
            return float(raw)

        text = str(raw).strip().lower().replace("受讓", "")
        text = text.replace("+", "")
        if "/" in text:
            parts = text.split("/")
            try:
                numeric_parts = [float(part) for part in parts if part]
            except ValueError:
                return None
            if not numeric_parts:
                return None
            return float(sum(numeric_parts) / len(numeric_parts))

        try:
            return float(text)
        except ValueError:
            return None

    def _normalize_odds(self, payload: dict[str, object]) -> tuple[float | None, float | None]:
        home_key = self._config.field_map["odds_home"]
        away_key = self._config.field_map["odds_away"]
        home = self._to_float(payload.get(home_key))
        away = self._to_float(payload.get(away_key))
        if home is None or away is None:
            return None, None

        semantic_key = self._config.field_map["side_semantic"]
        semantic_raw = str(payload.get(semantic_key, "")).strip().lower()
        semantic = self._config.side_map.get(semantic_raw, semantic_raw)
        if semantic == "away":
            return away, home
        return home, away

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 1.0:
            return None
        return parsed

    @staticmethod
    def _first_optional_float(payload: dict[str, object], keys: list[str]) -> float | None:
        for key in keys:
            if key not in payload:
                continue
            try:
                value = payload.get(key)
                if value in (None, ""):
                    continue
                return float(value)
            except (TypeError, ValueError):
                continue
        return None
