from __future__ import annotations

from typing import Protocol

from src.live_feed.models import ExternalMarketEvent, NormalizedMarketSnapshot


class HKJCAdapter(Protocol):
    """Map provider-specific payloads into internal normalized market snapshots."""

    def normalize_event(self, event: ExternalMarketEvent) -> NormalizedMarketSnapshot | None:
        """Return normalized snapshot, or None when event cannot be mapped safely."""
        ...

    def normalize_batch(self, events: list[ExternalMarketEvent]) -> list[NormalizedMarketSnapshot]:
        """Normalize a batch while skipping un-mappable records."""
        ...
