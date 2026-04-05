from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

SYNTHETIC_MARKERS = ("SYNTH", "SYNTHETIC", "MOCK", "SIM", "FAKE", "DUMMY")
REAL_MARKETS = ("HKJC", "NON_HKJC")


def _to_int(value: object, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _is_synthetic_token(value: object) -> bool:
    text = str(value).strip().upper()
    if not text:
        return False
    return any(marker in text for marker in SYNTHETIC_MARKERS)


def _is_real_market(value: object) -> bool:
    text = str(value).strip().upper()
    if not text:
        return False
    return any(token in text for token in REAL_MARKETS) and not _is_synthetic_token(text)


def inspect_training_data(input_path: Path) -> dict[str, object]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    frame = pd.read_csv(input_path)
    total_rows = int(len(frame))

    if total_rows == 0:
        raise ValueError("Input CSV has zero rows.")

    if "source_market" not in frame.columns:
        frame["source_market"] = "UNKNOWN"

    market_series = frame["source_market"].fillna("UNKNOWN").astype(str)
    real_mask = market_series.apply(_is_real_market)
    synthetic_mask = market_series.apply(_is_synthetic_token)

    real_rows = int(real_mask.sum())
    synthetic_rows = int(synthetic_mask.sum())
    unknown_rows = int(total_rows - real_rows - synthetic_rows)

    market_counts = (
        market_series.str.upper().value_counts(dropna=False).head(20).to_dict()
    )

    return {
        "input_path": str(input_path),
        "total_rows": total_rows,
        "real_rows": real_rows,
        "synthetic_rows": synthetic_rows,
        "unknown_rows": unknown_rows,
        "market_counts_top20": market_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate whether training data is real vs synthetic.")
    parser.add_argument("--input-path", type=Path, required=True, help="Feature CSV to inspect.")
    parser.add_argument(
        "--min-real-rows",
        type=int,
        default=300,
        help="If real rows reach this threshold, synthetic rows are no longer allowed in strict mode.",
    )
    parser.add_argument(
        "--strict-after-min-real",
        action="store_true",
        help="Fail when real rows >= min-real-rows and synthetic rows > 0.",
    )
    args = parser.parse_args()

    summary = inspect_training_data(args.input_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    real_rows = _to_int(summary.get("real_rows"))
    synthetic_rows = _to_int(summary.get("synthetic_rows"))
    total_rows = _to_int(summary.get("total_rows"))

    if real_rows <= 0:
        raise SystemExit("No real rows detected. Stop training.")

    if args.strict_after_min_real and real_rows >= args.min_real_rows and synthetic_rows > 0:
        raise SystemExit(
            "Synthetic rows are still present after real data threshold is reached. "
            "Please retrain on real-only data."
        )

    if synthetic_rows > 0 and real_rows < args.min_real_rows:
        print(
            "WARNING: synthetic rows detected. Allowed temporarily because real rows "
            f"({real_rows}) are below threshold ({args.min_real_rows})."
        )

    if total_rows < 100:
        print("WARNING: total rows below MVP minimum (100).")


if __name__ == "__main__":
    main()
