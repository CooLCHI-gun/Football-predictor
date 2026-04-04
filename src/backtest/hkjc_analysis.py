from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class HkjcBacktestRecommendation:
    status: str
    recommendation: str
    rationale: list[str]


def analyze_hkjc_backtest_summary(summary_df: pd.DataFrame) -> HkjcBacktestRecommendation:
    """Analyze HKJC backtest summary metrics and return conservative threshold/stake guidance."""
    if summary_df.empty:
        raise ValueError("Summary CSV is empty.")

    row = summary_df.iloc[0]
    roi = _to_float(row.get("roi"), 0.0)
    max_drawdown = _to_float(row.get("max_drawdown"), 1.0)
    pct_positive_clv = _to_optional_float(row.get("pct_positive_clv"))
    clv_score = _to_optional_float(row.get("clv_score"))
    clv_warning = str(row.get("clv_data_warning") or "").strip()

    weak_clv = (
        pct_positive_clv is None
        or clv_score is None
        or pct_positive_clv < 0.50
        or clv_score <= 0.0
    )
    weak_roi_or_risk = roi <= 0.0 or max_drawdown >= 0.12

    rationale = [
        f"roi={roi:.4f}",
        f"max_drawdown={max_drawdown:.4f}",
        f"pct_positive_clv={pct_positive_clv if pct_positive_clv is not None else 'NA'}",
        f"clv_score={clv_score if clv_score is not None else 'NA'}",
    ]
    if clv_warning:
        rationale.append(f"clv_data_warning={clv_warning}")

    if weak_clv and weak_roi_or_risk:
        return HkjcBacktestRecommendation(
            status="caution",
            recommendation=(
                "HKJC CLV and ROI are weak: consider raising min_edge_threshold from 0.01 to 0.015-0.02, "
                "and/or lowering fractional_kelly_factor (for example to 0.10-0.12). "
                "Keep max_stake_pct at or below 1% until CLV turns positive."
            ),
            rationale=rationale,
        )

    if weak_clv and not weak_roi_or_risk:
        return HkjcBacktestRecommendation(
            status="hold",
            recommendation=(
                "ROI is acceptable but CLV is not yet consistently positive: keep conservative staking "
                "(fractional_kelly_factor around 0.15, max_stake_pct 1%) and avoid increasing size."
            ),
            rationale=rationale,
        )

    if not weak_clv and roi > 0.0 and max_drawdown < 0.12:
        return HkjcBacktestRecommendation(
            status="stable",
            recommendation=(
                "HKJC CLV is consistently positive with ROI > 0: current thresholds are acceptable for low-stake live. "
                "Do not increase stake yet; require additional rolling samples before scaling."
            ),
            rationale=rationale,
        )

    return HkjcBacktestRecommendation(
        status="review",
        recommendation=(
            "Mixed HKJC signal: keep current conservative thresholds and rerun a narrow HKJC optimizer grid "
            "around edge 0.01-0.02 and confidence 0.05-0.12 before any stake change."
        ),
        rationale=rationale,
    )


def read_summary_and_analyze(summary_csv_path: str) -> HkjcBacktestRecommendation:
    summary_df = pd.read_csv(summary_csv_path)
    return analyze_hkjc_backtest_summary(summary_df)


def _to_float(value: object, default: float) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: object) -> float | None:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)
