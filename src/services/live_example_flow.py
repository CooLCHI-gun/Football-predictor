from __future__ import annotations

from pathlib import Path

from src.adapters.hkjc.default_adapter import DefaultHKJCAdapter
from src.live_feed.clients.csv_polling import CSVPollingFeedClient
from src.live_feed.repository import LiveFeedRepository
from src.services.alert_loop import AlertLoop
from src.services.live_prediction_service import LivePredictionService


def run_live_example() -> str:
    """Minimal end-to-end Phase 6 example using CSV snapshots."""

    feed_client = CSVPollingFeedClient(
        provider_name="demo_provider",
        snapshot_csv_path=Path("data/live/incoming/upcoming_markets.csv"),
    )
    adapter = DefaultHKJCAdapter()
    repository = LiveFeedRepository(
        snapshots_csv_path=Path("data/live/normalized/market_snapshots.csv"),
    )
    service = LivePredictionService(
        feed_client=feed_client,
        adapter=adapter,
        repository=repository,
        model_path=Path("artifacts/model_bundle.pkl"),
        output_dir=Path("artifacts/live"),
        lookahead_minutes=12 * 60,
        max_alerts=3,
    )

    loop = AlertLoop(service=service)
    result = loop.run_once()
    return (
        "live-cycle done | "
        f"fetched={result.fetched_events} normalized={result.normalized_snapshots} "
        f"inserted={result.inserted_snapshots} skipped={result.skipped_snapshots} "
        f"predictions={result.predictions_path} candidates={result.candidates_path}"
    )


if __name__ == "__main__":
    print(run_live_example())
