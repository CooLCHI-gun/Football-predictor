from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.adapters.hkjc.default_adapter import DefaultHKJCAdapter
from src.live_feed.models import ExternalMarketEvent
from src.live_feed.repository import LiveFeedRepository
from src.services.live_prediction_service import AlertSender, LivePredictionService


@dataclass(frozen=True)
class _FakeFeedClient:
    def fetch_events(self, *, as_of_utc: datetime, lookahead_minutes: int) -> list[ExternalMarketEvent]:
        kickoff = as_of_utc + timedelta(minutes=30)
        payload = {
            "provider_match_id": "match_001",
            "market_id": "ah_ft",
            "competition": "EPL",
            "kickoff_time_utc": kickoff.isoformat(),
            "snapshot_time_utc": as_of_utc.isoformat(),
            "home_team_name": "Home FC",
            "away_team_name": "Away FC",
            "handicap_line": "-0.5",
            "odds_home": 1.95,
            "odds_away": 1.95,
            "side_semantic": "home",
        }
        return [ExternalMarketEvent(provider_name="fake", payload=payload)]


@dataclass(frozen=True)
class _NoOpAlertSender(AlertSender):
    def send_from_predictions(
        self,
        *,
        predictions_path: Path,
        edge_threshold: float,
        confidence_threshold: float,
        max_alerts: int,
    ) -> str:
        return f"noop:{predictions_path.name}:{max_alerts}"


def test_live_feed_repository_is_idempotent(tmp_path: Path) -> None:
    from src.live_feed.models import NormalizedMarketSnapshot

    snapshot = NormalizedMarketSnapshot(
        provider_name="fake",
        provider_match_id="match_001",
        source_market="HKJC_LIKE",
        market_id="ah_ft",
        competition="EPL",
        kickoff_time_utc=datetime.now(timezone.utc),
        snapshot_time_utc=datetime.now(timezone.utc),
        home_team_name="Home FC",
        away_team_name="Away FC",
        handicap_line=-0.5,
        odds_home=1.95,
        odds_away=1.95,
    )
    repository = LiveFeedRepository(tmp_path / "snapshots.csv")

    first = repository.append_snapshots_idempotent([snapshot])
    second = repository.append_snapshots_idempotent([snapshot])

    assert first.inserted_rows == 1
    assert second.inserted_rows == 0
    assert second.skipped_duplicates == 1


def test_live_prediction_service_run_cycle(monkeypatch, tmp_path: Path) -> None:
    def _fake_load_model_bundle(model_path: Path) -> object:
        return object()

    def _fake_generate_prediction_frame(bundle: object, df: pd.DataFrame) -> pd.DataFrame:
        frame = df.copy()
        frame["predicted_side"] = "home"
        frame["model_probability"] = 0.62
        frame["confidence_score"] = 0.64
        frame["model_name"] = "logistic_regression"
        frame["model_approach"] = "direct_cover"
        return frame

    monkeypatch.setattr("src.services.live_prediction_service.load_model_bundle", _fake_load_model_bundle)
    monkeypatch.setattr("src.services.live_prediction_service.generate_prediction_frame", _fake_generate_prediction_frame)

    service = LivePredictionService(
        feed_client=_FakeFeedClient(),
        adapter=DefaultHKJCAdapter(),
        repository=LiveFeedRepository(tmp_path / "snapshots.csv"),
        model_path=tmp_path / "model_bundle.pkl",
        output_dir=tmp_path / "live",
        lookahead_minutes=120,
        max_alerts=3,
        edge_threshold=0.01,
        confidence_threshold=0.5,
        alert_sender=_NoOpAlertSender(),
    )

    result = service.run_cycle(as_of_utc=datetime.now(timezone.utc))

    assert result.fetched_events == 1
    assert result.normalized_snapshots == 1
    assert result.inserted_snapshots == 1
    assert result.predictions_path.exists()
    assert result.candidates_path.exists()
