from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

from src.live_feed.models import ExternalMarketEvent
from src.live_feed.providers.hkjc_provider import HKJCFootballProvider


class MarketFeedClient(Protocol):
    """Provider-agnostic market feed interface for live/near-live snapshots."""

    def fetch_market_snapshot(self, *, as_of_utc: datetime, poll_timeout_seconds: int) -> list[ExternalMarketEvent]:
        """Return raw provider-shaped market payloads around as_of time."""
        ...


@dataclass(frozen=True)
class MockMarketFeedClient:
    """Deterministic local mock feed for Phase 6 acceptance and sandbox tests."""

    provider_name: str = "mock"

    def fetch_market_snapshot(self, *, as_of_utc: datetime, poll_timeout_seconds: int) -> list[ExternalMarketEvent]:
        base_time = as_of_utc if as_of_utc.tzinfo is not None else as_of_utc.replace(tzinfo=timezone.utc)
        payloads: list[dict[str, object]] = []
        for index in range(1, 6):
            kickoff = base_time + timedelta(minutes=30 * index)
            handicap = -0.25 if index % 2 == 0 else -0.5
            odds_home = 1.88 + (0.02 * (index % 3))
            odds_away = 1.92 + (0.01 * ((index + 1) % 3))
            payloads.append(
                {
                    "provider_match_id": f"mock_match_{index:03d}",
                    "market_id": "ah_ft",
                    "competition": "MOCK_LEAGUE",
                    "kickoff_time_utc": kickoff.isoformat(),
                    "snapshot_time_utc": base_time.isoformat(),
                    "home_team_name": f"Mock Home {index}",
                    "away_team_name": f"Mock Away {index}",
                    "handicap_line": handicap,
                    "odds_home": round(odds_home, 3),
                    "odds_away": round(odds_away, 3),
                    "side_semantic": "home",
                    "source_label": "MOCK",
                }
            )
        return [ExternalMarketEvent(provider_name=self.provider_name, payload=item) for item in payloads]


@dataclass(frozen=True)
class CSVMarketFeedClient:
    """Near-live feed client that reads snapshots from a local CSV path."""

    provider_name: str
    snapshot_csv_path: Path

    def fetch_market_snapshot(self, *, as_of_utc: datetime, poll_timeout_seconds: int) -> list[ExternalMarketEvent]:
        if not self.snapshot_csv_path.exists():
            return []

        frame = pd.read_csv(self.snapshot_csv_path)
        if frame.empty:
            return []

        if "snapshot_time_utc" not in frame.columns:
            frame["snapshot_time_utc"] = datetime.now(timezone.utc).isoformat()

        payloads: list[ExternalMarketEvent] = []
        for row in frame.to_dict(orient="records"):
            payload = {str(key): value for key, value in dict(row).items()}
            payloads.append(ExternalMarketEvent(provider_name=self.provider_name, payload=payload))
        return payloads


def build_market_feed_client(provider: str) -> MarketFeedClient:
    """Factory for Phase 6 provider clients."""
    normalized = provider.strip().lower()
    if normalized == "mock":
        return MockMarketFeedClient(provider_name="mock")
    if normalized == "hkjc":
        return HKJCFootballProvider()
    if normalized == "csv":
        return CSVMarketFeedClient(
            provider_name="csv",
            snapshot_csv_path=Path("data/live/incoming/upcoming_markets.csv"),
        )
    raise ValueError(f"Unsupported provider: {provider}. Supported providers: hkjc,mock,csv")


__all__ = [
    "MarketFeedClient",
    "MockMarketFeedClient",
    "CSVMarketFeedClient",
    "HKJCFootballProvider",
    "build_market_feed_client",
]
