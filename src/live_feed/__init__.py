"""Phase 6 live feed abstractions and ingestion utilities."""

from src.live_feed.clients import MarketFeedClient, build_market_feed_client
from src.live_feed.models import ExternalMarketEvent, NormalizedMarketSnapshot
from src.live_feed.repository import IngestionResult, LiveFeedRepository
from src.live_feed.service import LivePredictionResult, LivePredictionService

__all__ = [
    "ExternalMarketEvent",
    "IngestionResult",
    "LiveFeedRepository",
    "MarketFeedClient",
    "NormalizedMarketSnapshot",
    "LivePredictionResult",
    "LivePredictionService",
    "build_market_feed_client",
]
