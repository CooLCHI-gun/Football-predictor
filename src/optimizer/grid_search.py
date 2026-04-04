from __future__ import annotations

import itertools
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.metrics import compute_clv_score
from src.backtest.engine import run_backtest_with_result
from src.config.settings import get_settings


LOGGER = logging.getLogger(__name__)

DEFAULT_EDGE_GRID = [0.01, 0.02, 0.03, 0.04]
DEFAULT_CONFIDENCE_GRID = [0.50, 0.53, 0.56, 0.60]
DEFAULT_POLICY_GRID = ["flat", "fractional_kelly", "vol_target"]
DEFAULT_KELLY_GRID = [0.15, 0.25, 0.35, 0.50]
DEFAULT_MAX_ALERTS_GRID = [1, 2, 3]
DEFAULT_MAX_STAKE_GRID = [0.01, 0.02]
DEFAULT_DAILY_EXPOSURE_GRID = [0.03, 0.05]


@dataclass(frozen=True)
class ObjectiveWeights:
    lambda_drawdown: float
    lambda_ror: float
    mu_clv: float
    mu_win_rate: float
    mu_placed_bets: float
    target_placed_bets: int
    lambda_low_bets: float
    min_bets_target: int


@dataclass(frozen=True)
class ParamSet:
    min_edge_threshold: float
    min_confidence_threshold: float
    max_alerts: int
    policy: str
    fractional_kelly_factor: float
    max_stake_pct: float
    daily_max_exposure_pct: float


def optimize_strategy(
    input_path: Path = Path("data/processed/features_phase3_full.csv"),
    output_dir: Path = Path("artifacts/optimizer"),
    prediction_cache_dir: Path = Path("artifacts/cache/backtest_predictions"),
    use_prediction_cache: bool = False,
    force_prediction_cache_refresh: bool = False,
    run_id: str | None = None,
    edge_grid: list[float] | None = None,
    confidence_grid: list[float] | None = None,
    policy_grid: list[str] | None = None,
    kelly_grid: list[float] | None = None,
    max_alerts_grid: list[int] | None = None,
    max_stake_grid: list[float] | None = None,
    daily_exposure_grid: list[float] | None = None,
    max_runs: int | None = None,
    dry_run: bool = False,
) -> str:
    """Run grid-search optimization over threshold and bankroll-policy parameters."""
    settings = get_settings()
    output_dir = _resolve_optimizer_output_dir(output_dir=output_dir, run_id=run_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    edge_grid = edge_grid or DEFAULT_EDGE_GRID
    confidence_grid = confidence_grid or DEFAULT_CONFIDENCE_GRID
    policy_grid = policy_grid or DEFAULT_POLICY_GRID
    kelly_grid = kelly_grid or DEFAULT_KELLY_GRID
    max_alerts_grid = max_alerts_grid or DEFAULT_MAX_ALERTS_GRID
    max_stake_grid = max_stake_grid or DEFAULT_MAX_STAKE_GRID
    daily_exposure_grid = daily_exposure_grid or DEFAULT_DAILY_EXPOSURE_GRID

    combinations = list(
        itertools.product(
            edge_grid,
            confidence_grid,
            max_alerts_grid,
            policy_grid,
            kelly_grid,
            max_stake_grid,
            daily_exposure_grid,
        )
    )
    if max_runs is not None:
        combinations = combinations[: max(0, int(max_runs))]

    if dry_run:
        return f"Optimizer dry-run: would execute {len(combinations)} runs."

    LOGGER.info("Optimization started with %s runs", len(combinations))

    weights = ObjectiveWeights(
        lambda_drawdown=settings.optimizer_lambda_drawdown,
        lambda_ror=float(getattr(settings, "optimizer_lambda_ror", 0.7)),
        mu_clv=float(getattr(settings, "optimizer_mu_clv", 0.3)),
        mu_win_rate=float(getattr(settings, "optimizer_mu_win_rate", 0.2)),
        mu_placed_bets=float(getattr(settings, "optimizer_mu_placed_bets", 0.2)),
        target_placed_bets=int(getattr(settings, "optimizer_target_placed_bets", 120)),
        lambda_low_bets=settings.optimizer_lambda_low_bets,
        min_bets_target=settings.optimizer_min_bets_target,
    )

    rows: list[dict[str, object]] = []
    best_row: dict[str, object] | None = None
    cache_hits = 0
    cache_misses = 0

    for edge, confidence, max_alerts, policy, kelly_factor, max_stake, max_daily_exposure in combinations:
        if policy == "flat" and kelly_factor != kelly_grid[0]:
            continue

        params = ParamSet(
            min_edge_threshold=float(edge),
            min_confidence_threshold=float(confidence),
            max_alerts=int(max_alerts),
            policy=policy,
            fractional_kelly_factor=float(kelly_factor),
            max_stake_pct=float(max_stake),
            daily_max_exposure_pct=float(max_daily_exposure),
        )

        run_name = (
            f"edge_{params.min_edge_threshold:.3f}_"
            f"conf_{params.min_confidence_threshold:.3f}_"
            f"alerts_{params.max_alerts}_"
            f"policy_{params.policy}_"
            f"kelly_{params.fractional_kelly_factor:.2f}_"
            f"cap_{params.max_stake_pct:.3f}_"
            f"daily_{params.daily_max_exposure_pct:.3f}"
        ).replace(".", "p")
        run_dir = output_dir / "runs" / run_name

        result = run_backtest_with_result(
            input_path=input_path,
            output_dir=run_dir,
            prediction_cache_dir=prediction_cache_dir,
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=force_prediction_cache_refresh,
            strategy_overrides={
                "min_edge_threshold": params.min_edge_threshold,
                "min_confidence_threshold": params.min_confidence_threshold,
                "max_concurrent_bets": params.max_alerts,
            },
            bankroll_overrides={
                "policy": params.policy,
                "fractional_kelly_factor": params.fractional_kelly_factor,
                "max_stake_pct": params.max_stake_pct,
                "daily_max_exposure_pct": params.daily_max_exposure_pct,
            },
        )

        summary = result.summary
        roi = _as_float(summary.get("roi"), 0.0)
        max_drawdown = _as_float(summary.get("max_drawdown"), 0.0)
        total_bets = _as_int(summary.get("total_bets_placed"), 0)
        win_rate = _as_float(summary.get("win_rate"), 0.0)
        risk_of_ruin_estimate = _as_optional_float(summary.get("risk_of_ruin_estimate"))
        avg_clv_pct = _as_optional_float(summary.get("avg_clv_pct"))
        median_clv_pct = _as_optional_float(summary.get("median_clv_pct"))
        pct_positive_clv = _as_optional_float(summary.get("pct_positive_clv"))
        clv_score = compute_clv_score(avg_clv_pct=avg_clv_pct, pct_positive_clv=pct_positive_clv)
        run_cache_hits = _as_int(summary.get("prediction_cache_hits"), 0)
        run_cache_misses = _as_int(summary.get("prediction_cache_misses"), 0)
        cache_hits += run_cache_hits
        cache_misses += run_cache_misses

        score = compute_objective_score(
            roi=roi,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            risk_of_ruin_estimate=risk_of_ruin_estimate,
            clv_score=clv_score,
            total_bets_placed=total_bets,
            weights=weights,
        )

        row: dict[str, object] = {
            **asdict(params),
            "roi": roi,
            "max_drawdown": max_drawdown,
            "risk_of_ruin_estimate": risk_of_ruin_estimate,
            "total_bets_placed": total_bets,
            "win_rate": win_rate,
            "avg_clv_pct": avg_clv_pct,
            "median_clv_pct": median_clv_pct,
            "pct_positive_clv": pct_positive_clv,
            "clv_score": clv_score,
            "prediction_cache_hits": run_cache_hits,
            "prediction_cache_misses": run_cache_misses,
            "score": score,
        }
        rows.append(row)

        if best_row is None or _as_float(row.get("score"), -1e9) > _as_float(best_row.get("score"), -1e9):
            best_row = row

    results_df = pd.DataFrame(rows).sort_values("score", ascending=False)
    params_results_path = output_dir / "params_results.csv"
    best_params_path = output_dir / "best_params.json"

    results_df.to_csv(params_results_path, index=False)
    if best_row is None:
        best_payload: dict[str, object] = {"note": "No valid parameter combinations were executed."}
    else:
        best_payload = best_row
    best_params_path.write_text(json.dumps(best_payload, indent=2), encoding="utf-8")

    LOGGER.info("Optimization completed. runs=%s best_score=%s", len(rows), _as_float(best_payload.get("score"), 0.0))

    cache_fragment = ""
    if use_prediction_cache:
        cache_fragment = f" | prediction_cache_hits={cache_hits} | prediction_cache_misses={cache_misses}"

    return f"Optimization complete: runs={len(rows)} | results={params_results_path} | best={best_params_path}{cache_fragment}"


def compute_objective_score(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    weights: ObjectiveWeights,
) -> float:
    low_bet_penalty = max(
        0.0,
        (weights.min_bets_target - total_bets_placed) / max(1, weights.min_bets_target),
    )
    risk_penalty = risk_of_ruin_estimate if risk_of_ruin_estimate is not None else 1.0
    clv_term = clv_score if clv_score is not None else 0.0
    placed_bets_term = min(1.0, total_bets_placed / max(1, weights.target_placed_bets))
    # Multi-objective score balances return quality (roi/win-rate/clv) against downside (drawdown/ruin/low-bet fragility).
    return (
        roi
        + (weights.mu_win_rate * win_rate)
        + (weights.mu_placed_bets * placed_bets_term)
        - (weights.lambda_drawdown * max_drawdown)
        - (weights.lambda_ror * risk_penalty)
        + (weights.mu_clv * clv_term)
        - (weights.lambda_low_bets * low_bet_penalty)
    )


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_optimizer_output_dir(output_dir: Path, run_id: str | None) -> Path:
    normalized_run_id = (run_id or "").strip()
    if not normalized_run_id:
        return output_dir
    return output_dir / normalized_run_id
