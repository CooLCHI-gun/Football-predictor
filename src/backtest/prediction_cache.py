from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def build_prediction_cache_key(payload: dict[str, Any]) -> str:
    """Build a stable hash key for prediction-cache lookup."""
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def prediction_cache_path(cache_root: Path, cache_key: str) -> Path:
    """Return cache file path under the cache root."""
    return cache_root / f"{cache_key}.csv"


def load_prediction_cache(path: Path) -> pd.DataFrame | None:
    """Load cached fold predictions if present and non-empty."""
    if not path.exists():
        return None

    df = pd.read_csv(path)
    if df.empty:
        return None

    if "kickoff_time_utc" in df.columns:
        df["kickoff_time_utc"] = pd.to_datetime(df["kickoff_time_utc"], utc=True)
    return df


def save_prediction_cache(df: pd.DataFrame, path: Path) -> None:
    """Persist fold predictions to cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
