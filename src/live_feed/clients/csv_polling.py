from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

from src.live_feed.client import MarketFeedClient
from src.live_feed.models import ExternalMarketEvent


@dataclass(frozen=True)
class CSVPollingFeedClient(MarketFeedClient):
    """Near-real-time feed client that polls a CSV snapshot file."""

    provider_name: str
    snapshot_csv_path: Path
    kickoff_column: str = "kickoff_time_utc"

    def fetch_events(self, *, as_of_utc: datetime, lookahead_minutes: int) -> list[ExternalMarketEvent]:
        if not self.snapshot_csv_path.exists():
            return []

        frame = pd.read_csv(self.snapshot_csv_path)
        if frame.empty:
            return []

        frame[self.kickoff_column] = pd.to_datetime(frame[self.kickoff_column], utc=True, errors="coerce")
        start = pd.Timestamp(as_of_utc)
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        else:
            start = start.tz_convert("UTC")
        end = start + pd.Timedelta(minutes=lookahead_minutes)

        filtered = frame[(frame[self.kickoff_column] >= start) & (frame[self.kickoff_column] <= end)].copy()
        events: list[ExternalMarketEvent] = []
        for row in filtered.to_dict(orient="records"):
            payload = {str(key): value for key, value in dict(row).items()}
            events.append(ExternalMarketEvent(provider_name=self.provider_name, payload=payload))
        return events
