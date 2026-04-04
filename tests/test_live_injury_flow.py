from __future__ import annotations

from datetime import datetime, timezone

from src.adapters.hkjc.default_adapter import DefaultHKJCAdapter
from src.live_feed.models import ExternalMarketEvent, NormalizedMarketSnapshot
from src.live_feed.service import LivePredictionService


def test_adapter_maps_injury_or_squad_fields() -> None:
    event = ExternalMarketEvent(
        provider_name="hkjc",
        payload={
            "provider_match_id": "m1",
            "market_id": "ah_ft",
            "competition": "League",
            "competition_ch": "聯賽",
            "kickoff_time_utc": "2026-04-04T12:00:00Z",
            "snapshot_time_utc": "2026-04-04T11:00:00Z",
            "home_team_name": "Home",
            "home_team_name_ch": "主",
            "away_team_name": "Away",
            "away_team_name_ch": "客",
            "handicap_line": -0.5,
            "odds_home": 1.95,
            "odds_away": 1.95,
            "side_semantic": "home",
            "squad_absence_score_home": 0.2,
            "injury_absence_index_away": 0.4,
        },
    )

    normalized = DefaultHKJCAdapter().normalize_event(event)

    assert normalized is not None
    assert normalized.squad_absence_score_home == 0.2
    assert normalized.injury_absence_index_away == 0.4


def test_live_feature_builder_keeps_injury_columns(tmp_path) -> None:
    service = LivePredictionService(
        model_path=tmp_path / "model.pkl",
        policy="flat",
        edge_threshold=0.0,
        confidence_threshold=0.0,
        max_alerts=3,
    )
    snapshot = NormalizedMarketSnapshot(
        provider_name="hkjc",
        provider_match_id="m1",
        source_market="HKJC_LIKE",
        market_id="ah_ft",
        competition="League",
        competition_ch="聯賽",
        kickoff_time_utc=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
        snapshot_time_utc=datetime(2026, 4, 4, 11, 0, tzinfo=timezone.utc),
        home_team_name="Home",
        home_team_name_ch="主",
        away_team_name="Away",
        away_team_name_ch="客",
        handicap_line=-0.5,
        odds_home=1.95,
        odds_away=1.95,
        injury_absence_index_home=0.3,
        injury_absence_index_away=0.1,
        squad_absence_score_home=0.2,
        squad_absence_score_away=0.4,
    )

    frame = service._build_live_features([snapshot])

    assert "injury_absence_index_home" in frame.columns
    assert "injury_absence_index_away" in frame.columns
    assert "squad_absence_score_home" in frame.columns
    assert "squad_absence_score_away" in frame.columns
    assert float(frame.iloc[0]["injury_absence_index_home"]) == 0.3
    assert float(frame.iloc[0]["squad_absence_score_away"]) == 0.4
