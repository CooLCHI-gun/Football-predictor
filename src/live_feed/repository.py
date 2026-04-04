from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.live_feed.models import NormalizedMarketSnapshot


@dataclass(frozen=True)
class IngestionResult:
    attempted_rows: int
    inserted_rows: int
    skipped_duplicates: int
    output_path: Path


class LiveFeedRepository:
    """CSV-first idempotent storage for normalized market snapshots."""

    def __init__(self, snapshots_csv_path: Path) -> None:
        self._snapshots_csv_path = snapshots_csv_path

    @property
    def snapshots_csv_path(self) -> Path:
        return self._snapshots_csv_path

    def append_snapshots_idempotent(self, snapshots: list[NormalizedMarketSnapshot]) -> IngestionResult:
        attempted = len(snapshots)
        if attempted == 0:
            return IngestionResult(
                attempted_rows=0,
                inserted_rows=0,
                skipped_duplicates=0,
                output_path=self._snapshots_csv_path,
            )

        self._snapshots_csv_path.parent.mkdir(parents=True, exist_ok=True)
        incoming_rows = [snapshot.to_row() for snapshot in snapshots]
        incoming_frame = pd.DataFrame(incoming_rows)

        existing_keys: set[str] = set()
        existing_frame = pd.DataFrame()
        if self._snapshots_csv_path.exists():
            existing_frame = self._safe_read_existing_csv(self._snapshots_csv_path)
            if "ingestion_key" in existing_frame.columns:
                existing_keys = set(existing_frame["ingestion_key"].dropna().astype(str).tolist())

        deduped_rows = [row for row in incoming_rows if str(row["ingestion_key"]) not in existing_keys]
        inserted = len(deduped_rows)
        skipped = attempted - inserted

        if inserted > 0:
            deduped_frame = pd.DataFrame(deduped_rows)

            # Schema can evolve over time (e.g., adding *_ch fields). If columns differ,
            # rewrite a normalized file instead of appending mismatched rows.
            if self._snapshots_csv_path.exists() and not existing_frame.empty:
                existing_columns = set(existing_frame.columns.tolist())
                incoming_columns = set(deduped_frame.columns.tolist())
                if existing_columns != incoming_columns:
                    all_columns = sorted(existing_columns.union(incoming_columns))
                    normalized_existing = existing_frame.reindex(columns=all_columns)
                    normalized_new = deduped_frame.reindex(columns=all_columns)
                    merged = pd.concat([normalized_existing, normalized_new], ignore_index=True)
                    merged.to_csv(self._snapshots_csv_path, index=False)
                else:
                    deduped_frame.to_csv(self._snapshots_csv_path, mode="a", index=False, header=False)
            else:
                write_header = not self._snapshots_csv_path.exists()
                deduped_frame.to_csv(self._snapshots_csv_path, mode="a", index=False, header=write_header)

        return IngestionResult(
            attempted_rows=attempted,
            inserted_rows=inserted,
            skipped_duplicates=skipped,
            output_path=self._snapshots_csv_path,
        )

    @staticmethod
    def _safe_read_existing_csv(path: Path) -> pd.DataFrame:
        try:
            return pd.read_csv(path)
        except Exception:
            # Tolerate historical malformed rows and keep pipeline running.
            return pd.read_csv(path, engine="python", on_bad_lines="skip")
