from __future__ import annotations

import itertools
import json
import logging
import statistics
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
DEFAULT_OUTER_ROLLING_WINDOWS = 1
DEFAULT_OUTER_MIN_WINDOW_MATCHES = 120


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
    mode: str = "BALANCED"
    winrate_min_win_rate: float = 0.53
    winrate_drawdown_cap: float = 0.12
    hard_min_bets: int = 120
    balanced_drawdown_cap: float = 0.12
    balanced_min_window_bets: int = 100
    balanced_min_roi: float = 0.0
    balanced_lambda_roi_std: float = 0.35
    balanced_lambda_win_rate_std: float = 0.25
    balanced_lambda_worst_window_roi: float = 0.30


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
    outer_rolling_windows: int | None = None,
    outer_min_window_matches: int | None = None,
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
    outer_rolling_windows = max(1, outer_rolling_windows or int(getattr(settings, "optimizer_outer_rolling_windows", DEFAULT_OUTER_ROLLING_WINDOWS)))
    outer_min_window_matches = max(
        30,
        outer_min_window_matches or int(getattr(settings, "optimizer_outer_min_window_matches", DEFAULT_OUTER_MIN_WINDOW_MATCHES)),
    )

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
        mode=str(getattr(settings, "optimizer_mode", "BALANCED")).upper(),
        winrate_min_win_rate=float(getattr(settings, "optimizer_winrate_min_win_rate", 0.53)),
        winrate_drawdown_cap=float(getattr(settings, "optimizer_winrate_drawdown_cap", 0.12)),
        hard_min_bets=int(getattr(settings, "optimizer_hard_min_bets", 120)),
        balanced_drawdown_cap=float(getattr(settings, "optimizer_balanced_drawdown_cap", 0.12)),
        balanced_min_window_bets=int(getattr(settings, "optimizer_balanced_min_window_bets", 100)),
        balanced_min_roi=float(getattr(settings, "optimizer_balanced_min_roi", 0.0)),
        balanced_lambda_roi_std=float(getattr(settings, "optimizer_balanced_lambda_roi_std", 0.35)),
        balanced_lambda_win_rate_std=float(getattr(settings, "optimizer_balanced_lambda_win_rate_std", 0.25)),
        balanced_lambda_worst_window_roi=float(
            getattr(settings, "optimizer_balanced_lambda_worst_window_roi", 0.30)
        ),
    )

    rows: list[dict[str, object]] = []
    best_row: dict[str, object] | None = None
    best_guarded_row: dict[str, object] | None = None
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

        summary = _evaluate_param_set_with_outer_rolling(
            input_path=input_path,
            run_dir=run_dir,
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
            outer_rolling_windows=outer_rolling_windows,
            outer_min_window_matches=outer_min_window_matches,
        )

        roi = _as_float(summary.get("roi"), 0.0)
        max_drawdown = _as_float(summary.get("max_drawdown"), 0.0)
        total_bets = _as_int(summary.get("total_bets_placed"), 0)
        win_rate = _as_float(summary.get("win_rate"), 0.0)
        min_window_bets = _as_int(summary.get("min_window_bets"), total_bets)
        worst_window_roi = _as_float(summary.get("worst_window_roi"), roi)
        worst_window_win_rate = _as_float(summary.get("worst_window_win_rate"), win_rate)
        roi_std = _as_float(summary.get("roi_std"), 0.0)
        win_rate_std = _as_float(summary.get("win_rate_std"), 0.0)
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
            min_window_bets=min_window_bets,
            roi_std=roi_std,
            win_rate_std=win_rate_std,
            worst_window_roi=worst_window_roi,
            weights=weights,
        )

        row: dict[str, object] = {
            **asdict(params),
            "roi": roi,
            "max_drawdown": max_drawdown,
            "risk_of_ruin_estimate": risk_of_ruin_estimate,
            "total_bets_placed": total_bets,
            "min_window_bets": min_window_bets,
            "win_rate": win_rate,
            "worst_window_roi": worst_window_roi,
            "worst_window_win_rate": worst_window_win_rate,
            "roi_std": roi_std,
            "win_rate_std": win_rate_std,
            "avg_clv_pct": avg_clv_pct,
            "median_clv_pct": median_clv_pct,
            "pct_positive_clv": pct_positive_clv,
            "clv_score": clv_score,
            "outer_rolling_windows": _as_int(summary.get("outer_rolling_windows"), 1),
            "outer_min_window_matches": outer_min_window_matches,
            "prediction_cache_hits": run_cache_hits,
            "prediction_cache_misses": run_cache_misses,
            "score": score,
        }
        rows.append(row)

        eligible_for_best = _as_int(row.get("min_window_bets"), 0) >= weights.hard_min_bets
        if eligible_for_best and weights.mode == "BALANCED_GUARDED":
            # Guardrails: require mean ROI above threshold; worst-window ROI is handled via stability penalty, not here.
            # balanced_min_window_bets adds a per-window bets floor alongside the global hard_min_bets gate.
            if max_drawdown <= weights.balanced_drawdown_cap and min_window_bets >= weights.balanced_min_window_bets and roi > weights.balanced_min_roi:
                if best_guarded_row is None or _as_float(row.get("score"), -1e9) > _as_float(best_guarded_row.get("score"), -1e9):
                    best_guarded_row = row

        if best_row is None and eligible_for_best:
            best_row = row
            continue

        if eligible_for_best and (
            best_row is None or _as_float(row.get("score"), -1e9) > _as_float(best_row.get("score"), -1e9)
        ):
            best_row = row

    # Fallback: if every run violates hard minimum-bets, keep top score anyway.
    if best_row is None and rows:
        best_row = max(rows, key=lambda item: _as_float(item.get("score"), -1e9))

    # For BALANCED_GUARDED, prefer the best guardrail-passing row when available.
    selection_reason: str | None = None
    passed_guardrails: bool | None = None
    if best_row is not None and weights.mode == "BALANCED_GUARDED":
        if best_guarded_row is not None:
            best_row = best_guarded_row
            selection_reason = "guardrail_best_score"
            passed_guardrails = True
        else:
            selection_reason = "fallback_best_score"
            passed_guardrails = False

    results_df = pd.DataFrame(rows).sort_values("score", ascending=False)
    params_results_path = output_dir / "params_results.csv"
    best_params_path = output_dir / "best_params.json"

    results_df.to_csv(params_results_path, index=False)
    if best_row is None:
        best_payload: dict[str, object] = {"note": "No valid parameter combinations were executed."}
    else:
        best_payload = dict(best_row)
        if selection_reason is not None:
            best_payload["selection_reason"] = selection_reason
            best_payload["passed_guardrails"] = passed_guardrails
    best_params_path.write_text(json.dumps(best_payload, indent=2), encoding="utf-8")

    LOGGER.info("Optimization completed. runs=%s best_score=%s", len(rows), _as_float(best_payload.get("score"), 0.0))

    cache_fragment = ""
    if use_prediction_cache:
        cache_fragment = f" | prediction_cache_hits={cache_hits} | prediction_cache_misses={cache_misses}"

    rolling_fragment = f" | outer_rolling_windows={outer_rolling_windows} | outer_min_window_matches={outer_min_window_matches}"
    return (
        f"Optimization complete: runs={len(rows)} | results={params_results_path} | "
        f"best={best_params_path}{rolling_fragment}{cache_fragment}"
    )


def _evaluate_param_set_with_outer_rolling(
    *,
    input_path: Path,
    run_dir: Path,
    prediction_cache_dir: Path,
    use_prediction_cache: bool,
    force_prediction_cache_refresh: bool,
    strategy_overrides: dict[str, object],
    bankroll_overrides: dict[str, object],
    outer_rolling_windows: int,
    outer_min_window_matches: int,
) -> dict[str, object]:
    if outer_rolling_windows <= 1:
        result = run_backtest_with_result(
            input_path=input_path,
            output_dir=run_dir,
            prediction_cache_dir=prediction_cache_dir,
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=force_prediction_cache_refresh,
            strategy_overrides=strategy_overrides,
            bankroll_overrides=bankroll_overrides,
        )
        total_bets = _as_int(result.summary.get("total_bets_placed"), 0)
        roi = _as_float(result.summary.get("roi"), 0.0)
        win_rate = _as_float(result.summary.get("win_rate"), 0.0)
        max_drawdown = _as_float(result.summary.get("max_drawdown"), 0.0)
        return {
            **result.summary,
            "roi": roi,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "outer_rolling_windows": 1,
            "min_window_bets": total_bets,
            "worst_window_roi": roi,
            "worst_window_win_rate": win_rate,
            "roi_std": 0.0,
            "win_rate_std": 0.0,
        }

    frame = pd.read_csv(input_path)
    total_rows = len(frame)
    if total_rows < outer_min_window_matches:
        result = run_backtest_with_result(
            input_path=input_path,
            output_dir=run_dir,
            prediction_cache_dir=prediction_cache_dir,
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=force_prediction_cache_refresh,
            strategy_overrides=strategy_overrides,
            bankroll_overrides=bankroll_overrides,
        )
        total_bets = _as_int(result.summary.get("total_bets_placed"), 0)
        roi = _as_float(result.summary.get("roi"), 0.0)
        win_rate = _as_float(result.summary.get("win_rate"), 0.0)
        max_drawdown = _as_float(result.summary.get("max_drawdown"), 0.0)
        return {
            **result.summary,
            "roi": roi,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "outer_rolling_windows": 1,
            "min_window_bets": total_bets,
            "worst_window_roi": roi,
            "worst_window_win_rate": win_rate,
            "roi_std": 0.0,
            "win_rate_std": 0.0,
        }

    window_size = max(outer_min_window_matches, total_rows // outer_rolling_windows)
    window_size = min(window_size, total_rows)
    step = 1 if outer_rolling_windows <= 1 else max(1, (total_rows - window_size) // (outer_rolling_windows - 1))

    summaries: list[dict[str, object]] = []
    for window_idx in range(outer_rolling_windows):
        start = min(window_idx * step, max(0, total_rows - window_size))
        end = min(total_rows, start + window_size)
        subset = frame.iloc[start:end].copy()
        if len(subset) < outer_min_window_matches:
            continue

        window_input_path = run_dir / f"outer_window_{window_idx + 1}.csv"
        window_output_dir = run_dir / f"outer_window_{window_idx + 1}"
        window_input_path.parent.mkdir(parents=True, exist_ok=True)
        subset.to_csv(window_input_path, index=False)

        result = run_backtest_with_result(
            input_path=window_input_path,
            output_dir=window_output_dir,
            prediction_cache_dir=prediction_cache_dir,
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=force_prediction_cache_refresh,
            strategy_overrides=strategy_overrides,
            bankroll_overrides=bankroll_overrides,
        )
        summaries.append(result.summary)

    if not summaries:
        result = run_backtest_with_result(
            input_path=input_path,
            output_dir=run_dir,
            prediction_cache_dir=prediction_cache_dir,
            use_prediction_cache=use_prediction_cache,
            force_prediction_cache_refresh=force_prediction_cache_refresh,
            strategy_overrides=strategy_overrides,
            bankroll_overrides=bankroll_overrides,
        )
        total_bets = _as_int(result.summary.get("total_bets_placed"), 0)
        roi = _as_float(result.summary.get("roi"), 0.0)
        win_rate = _as_float(result.summary.get("win_rate"), 0.0)
        max_drawdown = _as_float(result.summary.get("max_drawdown"), 0.0)
        return {
            **result.summary,
            "roi": roi,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "outer_rolling_windows": 1,
            "min_window_bets": total_bets,
            "worst_window_roi": roi,
            "worst_window_win_rate": win_rate,
            "roi_std": 0.0,
            "win_rate_std": 0.0,
        }

    roi_values = [_as_float(item.get("roi"), 0.0) for item in summaries]
    win_rate_values = [_as_float(item.get("win_rate"), 0.0) for item in summaries]
    drawdown_values = [_as_float(item.get("max_drawdown"), 0.0) for item in summaries]
    bets_values = [_as_int(item.get("total_bets_placed"), 0) for item in summaries]
    ror_values = [
        _as_optional_float(item.get("risk_of_ruin_estimate"))
        for item in summaries
        if _as_optional_float(item.get("risk_of_ruin_estimate")) is not None
    ]
    clv_avg_values = [
        _as_optional_float(item.get("avg_clv_pct"))
        for item in summaries
        if _as_optional_float(item.get("avg_clv_pct")) is not None
    ]
    clv_median_values = [
        _as_optional_float(item.get("median_clv_pct"))
        for item in summaries
        if _as_optional_float(item.get("median_clv_pct")) is not None
    ]
    positive_clv_values = [
        _as_optional_float(item.get("pct_positive_clv"))
        for item in summaries
        if _as_optional_float(item.get("pct_positive_clv")) is not None
    ]

    return {
        "roi": statistics.fmean(roi_values) if roi_values else 0.0,
        "win_rate": statistics.fmean(win_rate_values) if win_rate_values else 0.0,
        "max_drawdown": max(drawdown_values) if drawdown_values else 0.0,
        "total_bets_placed": int(round(statistics.fmean(bets_values))) if bets_values else 0,
        "min_window_bets": min(bets_values) if bets_values else 0,
        "worst_window_roi": min(roi_values) if roi_values else 0.0,
        "worst_window_win_rate": min(win_rate_values) if win_rate_values else 0.0,
        "roi_std": statistics.pstdev(roi_values) if len(roi_values) > 1 else 0.0,
        "win_rate_std": statistics.pstdev(win_rate_values) if len(win_rate_values) > 1 else 0.0,
        "risk_of_ruin_estimate": max(ror_values) if ror_values else None,
        "avg_clv_pct": statistics.fmean(clv_avg_values) if clv_avg_values else None,
        "median_clv_pct": statistics.fmean(clv_median_values) if clv_median_values else None,
        "pct_positive_clv": statistics.fmean(positive_clv_values) if positive_clv_values else None,
        "prediction_cache_hits": sum(_as_int(item.get("prediction_cache_hits"), 0) for item in summaries),
        "prediction_cache_misses": sum(_as_int(item.get("prediction_cache_misses"), 0) for item in summaries),
        "outer_rolling_windows": len(summaries),
    }


def compute_objective_score(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    min_window_bets: int | None = None,
    roi_std: float | None = None,
    win_rate_std: float | None = None,
    worst_window_roi: float | None = None,
    weights: ObjectiveWeights,
) -> float:
    min_window_bets = total_bets_placed if min_window_bets is None else min_window_bets
    if min_window_bets < weights.hard_min_bets:
        return -1000.0 - (weights.hard_min_bets - min_window_bets)

    if weights.mode == "WINRATE_GUARDED":
        return compute_objective_score_winrate_guarded(
            roi=roi,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            risk_of_ruin_estimate=risk_of_ruin_estimate,
            clv_score=clv_score,
            total_bets_placed=total_bets_placed,
            weights=weights,
        )

    if weights.mode == "BALANCED_GUARDED":
        return compute_objective_score_balanced_guarded(
            roi=roi,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            risk_of_ruin_estimate=risk_of_ruin_estimate,
            clv_score=clv_score,
            total_bets_placed=total_bets_placed,
            roi_std=roi_std,
            win_rate_std=win_rate_std,
            worst_window_roi=worst_window_roi,
            weights=weights,
        )

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


def compute_objective_score_winrate_guarded(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    weights: ObjectiveWeights,
) -> float:
    if max_drawdown > weights.winrate_drawdown_cap:
        return -500.0 - (max_drawdown - weights.winrate_drawdown_cap) * 100.0

    if win_rate < weights.winrate_min_win_rate:
        return -300.0 - (weights.winrate_min_win_rate - win_rate) * 100.0

    risk_penalty = risk_of_ruin_estimate if risk_of_ruin_estimate is not None else 1.0
    clv_term = clv_score if clv_score is not None else 0.0
    placed_bets_term = min(1.0, total_bets_placed / max(1, weights.target_placed_bets))
    low_bet_penalty = max(
        0.0,
        (weights.min_bets_target - total_bets_placed) / max(1, weights.min_bets_target),
    )

    return (
        (1.50 * win_rate)
        + (0.45 * roi)
        + (0.15 * clv_term)
        + (0.25 * placed_bets_term)
        - (1.20 * max_drawdown)
        - (0.80 * risk_penalty)
        - (0.20 * low_bet_penalty)
    )


def compute_objective_score_balanced_guarded(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    roi_std: float | None,
    win_rate_std: float | None,
    worst_window_roi: float | None,
    weights: ObjectiveWeights,
) -> float:
    low_bet_penalty = max(
        0.0,
        (weights.min_bets_target - total_bets_placed) / max(1, weights.min_bets_target),
    )
    risk_penalty = risk_of_ruin_estimate if risk_of_ruin_estimate is not None else 1.0
    clv_term = clv_score if clv_score is not None else 0.0
    placed_bets_term = min(1.0, total_bets_placed / max(1, weights.target_placed_bets))
    roi_std_value = roi_std if roi_std is not None else 0.0
    win_rate_std_value = win_rate_std if win_rate_std is not None else 0.0
    worst_window_roi_value = worst_window_roi if worst_window_roi is not None else roi

    base_score = (
        roi
        + (weights.mu_win_rate * win_rate)
        + (weights.mu_placed_bets * placed_bets_term)
        - (weights.lambda_drawdown * max_drawdown)
        - (weights.lambda_ror * risk_penalty)
        + (weights.mu_clv * clv_term)
        - (weights.lambda_low_bets * low_bet_penalty)
    )

    stability_penalty = (
        weights.balanced_lambda_roi_std * roi_std_value
        + weights.balanced_lambda_win_rate_std * win_rate_std_value
        + weights.balanced_lambda_worst_window_roi
        * max(0.0, weights.balanced_min_roi - worst_window_roi_value)
    )

    return base_score - stability_penalty


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
