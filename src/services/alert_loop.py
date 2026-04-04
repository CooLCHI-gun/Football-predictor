from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.services.live_prediction_service import LiveCycleResult, LivePredictionService


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertLoopConfig:
    poll_seconds: int = 60
    max_cycles: int | None = None


class AlertLoop:
    """Simple polling loop wrapper around LivePredictionService."""

    def __init__(self, service: LivePredictionService, config: AlertLoopConfig | None = None) -> None:
        self._service = service
        self._config = config or AlertLoopConfig()

    def run_once(self) -> LiveCycleResult:
        result = self._service.run_cycle()
        LOGGER.info(
            "Live cycle complete: fetched=%s normalized=%s inserted=%s skipped=%s",
            result.fetched_events,
            result.normalized_snapshots,
            result.inserted_snapshots,
            result.skipped_snapshots,
        )
        return result

    def run_forever(self) -> None:
        cycles = 0
        while True:
            self.run_once()
            cycles += 1
            if self._config.max_cycles is not None and cycles >= self._config.max_cycles:
                return
            time.sleep(max(1, self._config.poll_seconds))
