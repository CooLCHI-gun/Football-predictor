from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.live_feed.models import ExternalMarketEvent


class MarketFeedClient(Protocol):
    """Generic live/near-live market feed client interface for Phase 6."""

    def fetch_events(self, *, as_of_utc: datetime, lookahead_minutes: int) -> list[ExternalMarketEvent]:
        """Fetch raw events from upstream providers for the next lookahead window."""
        ...
