from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.strategy.settlement import settle_handicap_bet


@dataclass(frozen=True)
class HkjcHistoryEvaluation:
    total_rows: int
    evaluated_rows: int
    hits: int
    hit_rate: float
    flipped_hits: int
    flipped_hit_rate: float
    hit_rate_delta_if_flipped: float
    active_mode: str
    active_hits: int
    active_hit_rate: float
    roi_rows: int
    roi: float | None
    flipped_roi: float | None
    roi_delta_if_flipped: float | None


def evaluate_hkjc_model_on_history(prediction_df: pd.DataFrame, *, flip_side: bool = False) -> HkjcHistoryEvaluation:
    """Evaluate HKJC prediction output and compare base-side vs flipped-side performance."""
    if prediction_df.empty:
        raise ValueError("Prediction CSV is empty.")

    required_cols = {"predicted_side", "target_handicap_side"}
    missing = sorted(col for col in required_cols if col not in prediction_df.columns)
    if missing:
        raise ValueError(f"Prediction CSV missing required columns: {', '.join(missing)}")

    scored_df = prediction_df.copy()
    scored_df["predicted_side"] = scored_df["predicted_side"].astype(str).str.strip().str.lower()
    scored_df["target_handicap_side"] = scored_df["target_handicap_side"].astype(str).str.strip().str.lower()
    scored_df = scored_df[
        scored_df["predicted_side"].isin({"home", "away"})
        & scored_df["target_handicap_side"].isin({"home", "away"})
    ].copy()

    scored_df["flipped_side"] = scored_df["predicted_side"].map({"home": "away", "away": "home"})
    scored_df["is_hit"] = (scored_df["predicted_side"] == scored_df["target_handicap_side"]).astype(int)
    scored_df["is_hit_flipped"] = (scored_df["flipped_side"] == scored_df["target_handicap_side"]).astype(int)

    evaluated_rows = int(len(scored_df))
    hits = int(scored_df["is_hit"].sum())
    flipped_hits = int(scored_df["is_hit_flipped"].sum())
    hit_rate = (hits / evaluated_rows) if evaluated_rows else 0.0
    flipped_hit_rate = (flipped_hits / evaluated_rows) if evaluated_rows else 0.0

    roi, roi_rows = _compute_flat_stake_roi(scored_df=scored_df, side_column="predicted_side")
    flipped_roi, _ = _compute_flat_stake_roi(scored_df=scored_df, side_column="flipped_side")

    active_mode = "flipped" if flip_side else "base"
    active_hits = flipped_hits if flip_side else hits
    active_hit_rate = flipped_hit_rate if flip_side else hit_rate

    return HkjcHistoryEvaluation(
        total_rows=int(len(prediction_df)),
        evaluated_rows=evaluated_rows,
        hits=hits,
        hit_rate=hit_rate,
        flipped_hits=flipped_hits,
        flipped_hit_rate=flipped_hit_rate,
        hit_rate_delta_if_flipped=flipped_hit_rate - hit_rate,
        active_mode=active_mode,
        active_hits=active_hits,
        active_hit_rate=active_hit_rate,
        roi_rows=roi_rows,
        roi=roi,
        flipped_roi=flipped_roi,
        roi_delta_if_flipped=(None if roi is None or flipped_roi is None else flipped_roi - roi),
    )


def format_hkjc_history_evaluation(result: HkjcHistoryEvaluation) -> list[str]:
    lines = [
        "HKJC history prediction analysis",
        f"total_rows={result.total_rows}",
        f"evaluated_rows={result.evaluated_rows}",
        f"base_hits={result.hits}",
        f"base_hit_rate={result.hit_rate:.4f}",
        f"flipped_hits={result.flipped_hits}",
        f"flipped_hit_rate={result.flipped_hit_rate:.4f}",
        f"hit_rate_delta_if_flipped={result.hit_rate_delta_if_flipped:.4f}",
        f"active_mode={result.active_mode}",
        f"active_hits={result.active_hits}",
        f"active_hit_rate={result.active_hit_rate:.4f}",
        f"roi_rows={result.roi_rows}",
        f"base_roi={_format_optional_float(result.roi)}",
        f"flipped_roi={_format_optional_float(result.flipped_roi)}",
        f"roi_delta_if_flipped={_format_optional_float(result.roi_delta_if_flipped)}",
    ]
    return lines


def _compute_flat_stake_roi(*, scored_df: pd.DataFrame, side_column: str) -> tuple[float | None, int]:
    has_home_goals = "ft_home_goals" in scored_df.columns
    has_away_goals = "ft_away_goals" in scored_df.columns
    has_line = "handicap_close_line" in scored_df.columns or "handicap_open_line" in scored_df.columns
    has_odds = (
        "odds_home_close" in scored_df.columns
        or "odds_away_close" in scored_df.columns
        or "odds_home_open" in scored_df.columns
        or "odds_away_open" in scored_df.columns
    )
    if not (has_home_goals and has_away_goals and has_line and has_odds):
        return None, 0

    stake_total = 0.0
    pnl_total = 0.0
    rows_used = 0

    for _, row in scored_df.iterrows():
        side = str(row.get(side_column) or "").strip().lower()
        if side not in {"home", "away"}:
            continue

        line = _pick_number(row, ["handicap_close_line", "handicap_open_line"])
        odds = _pick_number(row, [f"odds_{side}_close", f"odds_{side}_open"])
        home_goals = _to_int(row.get("ft_home_goals"))
        away_goals = _to_int(row.get("ft_away_goals"))

        if line is None or odds is None or home_goals is None or away_goals is None:
            continue
        if odds <= 1.0:
            continue

        settlement = settle_handicap_bet(
            home_goals=home_goals,
            away_goals=away_goals,
            handicap_side=side,
            handicap_line=line,
            odds=odds,
            stake=1.0,
        )
        rows_used += 1
        stake_total += 1.0
        pnl_total += float(settlement.pnl)

    if rows_used == 0 or stake_total <= 0:
        return None, 0
    return (pnl_total / stake_total), rows_used


def _pick_number(row: pd.Series, candidates: list[str]) -> float | None:
    for column in candidates:
        if column not in row.index:
            continue
        value = row.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed):
            continue
        return float(parsed)
    return None


def _to_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.4f}"
