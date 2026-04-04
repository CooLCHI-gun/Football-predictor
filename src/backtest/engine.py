from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from src.backtest.folds import build_walkforward_fold_manifest
from src.backtest.metrics import summarize_backtest
from src.backtest.prediction_cache import (
    build_prediction_cache_key,
    load_prediction_cache,
    prediction_cache_path,
    save_prediction_cache,
)
from src.bankroll.controls import (
    allows_daily_exposure,
    allows_daily_stop_loss,
    apply_stake_bounds,
    should_halt_by_drawdown,
)
from src.bankroll.models import BankrollPolicyConfig, BankrollState, RiskControlsConfig, StakePolicyName
from src.bankroll.policies import compute_stake
from src.config.settings import get_settings
from src.config.strategy import load_bankroll_defaults, load_strategy_thresholds
from src.models.baselines import ModelBundle, generate_prediction_frame, train_model_bundle
from src.strategy.rules import maybe_flip_hkjc_side
from src.strategy.settlement import settle_handicap_bet


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestArtifacts:
    predictions_path: Path
    trade_log_path: Path
    summary_path: Path


@dataclass(frozen=True)
class BacktestRunResult:
    artifacts: BacktestArtifacts
    summary: dict[str, object]
    message: str


def run_backtest(
    input_path: Path = Path("data/processed/features_phase3.csv"),
    output_dir: Path = Path("artifacts/backtest"),
    run_id: str | None = None,
    model_name: str | None = None,
    approach: str | None = None,
    include_market_features: bool | None = None,
    strategy_overrides: dict[str, float | int | bool] | None = None,
    bankroll_overrides: dict[str, float | str | None] | None = None,
    flip_hkjc_side: bool | None = None,
) -> str:
    """Run walk-forward backtest and return a concise human-readable report string."""
    result = run_backtest_with_result(
        input_path=input_path,
        output_dir=output_dir,
        run_id=run_id,
        model_name=model_name,
        approach=approach,
        include_market_features=include_market_features,
        strategy_overrides=strategy_overrides,
        bankroll_overrides=bankroll_overrides,
        flip_hkjc_side=flip_hkjc_side,
    )
    return result.message


def run_backtest_with_result(
    input_path: Path = Path("data/processed/features_phase3.csv"),
    output_dir: Path = Path("artifacts/backtest"),
    prediction_cache_dir: Path = Path("artifacts/cache/backtest_predictions"),
    use_prediction_cache: bool = False,
    force_prediction_cache_refresh: bool = False,
    run_id: str | None = None,
    model_name: str | None = None,
    approach: str | None = None,
    include_market_features: bool | None = None,
    strategy_overrides: dict[str, float | int | bool] | None = None,
    bankroll_overrides: dict[str, float | str | None] | None = None,
    flip_hkjc_side: bool | None = None,
) -> BacktestRunResult:
    """Run walk-forward backtest and return structured artifacts plus summary payload."""
    settings = get_settings()
    thresholds = load_strategy_thresholds()
    bankroll_defaults = load_bankroll_defaults()
    strategy_overrides = strategy_overrides or {}
    bankroll_overrides = bankroll_overrides or {}
    flip_hkjc_side = settings.flip_hkjc_side if flip_hkjc_side is None else flip_hkjc_side

    LOGGER.info("Backtest loading input: %s", input_path)
    feature_df = load_and_prepare_feature_df(
        input_path=input_path,
        dataset_scope=str(getattr(settings, "backtest_dataset_scope", "AUTO")),
        default_source_market=str(getattr(settings, "data_source_tag", "NON_HKJC")),
    )

    model_name = model_name or settings.model_name
    approach = approach or settings.model_approach
    include_market_features = settings.include_market_features if include_market_features is None else include_market_features

    if len(feature_df) < settings.min_train_matches + settings.walkforward_test_window:
        raise ValueError(
            "Not enough feature rows for walk-forward backtest. "
            f"Need at least {settings.min_train_matches + settings.walkforward_test_window}, got {len(feature_df)}."
        )

    LOGGER.info("Backtest started: rows=%s model=%s approach=%s", len(feature_df), model_name, approach)

    fold_manifest = build_walkforward_fold_manifest(
        total_rows=len(feature_df),
        min_train_matches=settings.min_train_matches,
        purge_gap_matches=settings.purge_gap_matches,
        walkforward_test_window=settings.walkforward_test_window,
        retrain_every_matches=settings.retrain_every_matches,
    )

    predictions_frames: list[pd.DataFrame] = []
    prediction_cache_hits = 0
    prediction_cache_misses = 0
    previous_bundle: ModelBundle | None = None

    for fold in fold_manifest:
        train_df = feature_df.iloc[: fold.train_end].copy()
        test_df = feature_df.iloc[fold.test_start : fold.test_end].copy()

        prediction_df: pd.DataFrame | None = None
        cache_path: Path | None = None

        if use_prediction_cache:
            cache_key = build_prediction_cache_key(
                {
                    "input_path": str(input_path),
                    "model_name": model_name,
                    "approach": approach,
                    "include_market_features": include_market_features,
                    "fold_index": fold.fold_index,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                }
            )
            cache_path = prediction_cache_path(prediction_cache_dir, cache_key)

            if not force_prediction_cache_refresh:
                prediction_df = load_prediction_cache(cache_path)

        if prediction_df is None:
            if use_prediction_cache:
                prediction_cache_misses += 1

            bundle, report = train_model_bundle(
                df=train_df,
                model_name=model_name,
                approach=approach,
                include_market_features=include_market_features,
                previous_bundle=previous_bundle,
            )
            previous_bundle = bundle
            prediction_df = generate_prediction_frame(bundle=bundle, df=test_df)
            prediction_df["fold_train_size"] = len(train_df)
            prediction_df["fold_retrain_status"] = report.note or "trained"

            if use_prediction_cache and cache_path is not None:
                save_prediction_cache(prediction_df, cache_path)
        else:
            prediction_cache_hits += 1
            if "fold_train_size" not in prediction_df.columns:
                prediction_df["fold_train_size"] = len(train_df)
            if "fold_retrain_status" not in prediction_df.columns:
                prediction_df["fold_retrain_status"] = "loaded_from_cache"

        predictions_frames.append(prediction_df)

    all_predictions = pd.concat(predictions_frames, ignore_index=True) if predictions_frames else pd.DataFrame()
    if not all_predictions.empty:
        all_predictions["original_predicted_side"] = all_predictions["predicted_side"].astype(str)
        all_predictions["effective_predicted_side"] = all_predictions.apply(
            lambda row: maybe_flip_hkjc_side(
                predicted_side=str(row.get("predicted_side", "")),
                source_market=str(row.get("source_market", "")),
                flip_hkjc_side=flip_hkjc_side,
            )
            or str(row.get("predicted_side", "")),
            axis=1,
        )
        all_predictions["flip_hkjc_side_enabled"] = bool(flip_hkjc_side)

    trade_log, summary = replay_strategy_from_predictions(
        predictions=all_predictions,
        bankroll_initial=_as_float(
            bankroll_overrides.get("bankroll_initial"),
            bankroll_defaults.bankroll_initial,
        ),
        min_edge_threshold=_as_float(
            strategy_overrides.get("min_edge_threshold"),
            thresholds.min_edge_threshold,
        ),
        min_confidence_threshold=_as_float(
            strategy_overrides.get("min_confidence_threshold"),
            thresholds.min_confidence_threshold,
        ),
        max_concurrent_bets=_as_int(
            strategy_overrides.get("max_concurrent_bets"),
            thresholds.max_concurrent_bets,
        ),
        skip_missing_data=_as_bool(
            strategy_overrides.get("skip_missing_data"),
            thresholds.skip_missing_data,
        ),
        flip_hkjc_side=flip_hkjc_side,
        odds_source=settings.odds_source,
        bankroll_policy=BankrollPolicyConfig(
            policy=_as_policy_name(
                bankroll_overrides.get("policy"),
                bankroll_defaults.bankroll_mode,
            ),
            flat_stake=_as_float(bankroll_overrides.get("flat_stake"), thresholds.flat_stake),
            fixed_fraction_pct=_as_float(
                bankroll_overrides.get("fixed_fraction_pct"),
                bankroll_defaults.bankroll_fixed_fraction_pct,
            ),
            fractional_kelly_factor=_as_float(
                bankroll_overrides.get("fractional_kelly_factor"),
                bankroll_defaults.fractional_kelly_factor,
            ),
            vol_target_rolling_window_bets=_as_int(
                bankroll_overrides.get("vol_target_rolling_window_bets"),
                bankroll_defaults.vol_target_rolling_window_bets,
            ),
            vol_target_target_per_bet_vol=_as_float(
                bankroll_overrides.get("vol_target_target_per_bet_vol"),
                bankroll_defaults.vol_target_target_per_bet_vol,
            ),
        ),
        controls=RiskControlsConfig(
            max_stake_pct=_as_float(
                bankroll_overrides.get("max_stake_pct"),
                bankroll_defaults.max_stake_pct,
            ),
            min_stake_amount=_as_float(
                bankroll_overrides.get("min_stake_amount"),
                bankroll_defaults.min_stake_amount,
            ),
            daily_max_exposure_pct=_as_float(
                bankroll_overrides.get("daily_max_exposure_pct"),
                bankroll_defaults.daily_max_exposure_pct,
            ),
            max_drawdown_pct=_as_float(
                bankroll_overrides.get("max_drawdown_pct"),
                bankroll_defaults.max_drawdown_pct,
            ),
            daily_stop_loss_pct=_as_optional_float(
                bankroll_overrides.get("daily_stop_loss_pct"),
                bankroll_defaults.daily_stop_loss_pct,
            ),
        ),
    )
    summary["prediction_cache_enabled"] = use_prediction_cache
    summary["prediction_cache_hits"] = prediction_cache_hits
    summary["prediction_cache_misses"] = prediction_cache_misses
    summary["flip_hkjc_side_enabled"] = bool(flip_hkjc_side)

    artifacts = write_backtest_artifacts(
        predictions=all_predictions,
        trade_log=trade_log,
        summary=summary,
        output_dir=_resolve_backtest_output_dir(output_dir=output_dir, run_id=run_id),
    )

    LOGGER.info(
        "Backtest completed: matches=%s bets=%s roi=%.4f",
        summary["total_matches_evaluated"],
        summary["total_bets_placed"],
        float(summary["roi"]),
    )

    summary_lines = [
        f"Backtest complete: model={model_name} approach={approach}",
        f"Predictions CSV: {artifacts.predictions_path}",
        f"Trade log CSV: {artifacts.trade_log_path}",
        f"Summary CSV: {artifacts.summary_path}",
        f"Matches evaluated: {summary['total_matches_evaluated']}",
        f"Bets placed: {summary['total_bets_placed']}",
        f"Wins/Losses/Pushes/HalfWins/HalfLosses: {summary['wins']}/{summary['losses']}/{summary['pushes']}/{summary['half_wins']}/{summary['half_losses']}",
        f"Win rate: {summary['win_rate']:.2%}",
        f"ROI: {summary['roi']:.2%}",
        f"Brier score: {summary['brier_score']}",
        f"Log loss: {summary['log_loss']}",
        f"Total stake: {summary['total_stake']:.2f}",
        f"Total return: {summary['total_return']:.2f}",
        f"Net profit: {summary['net_profit']:.2f}",
        f"Max drawdown: {summary['max_drawdown']:.2%}",
        f"Bankroll summary: {json.dumps(summary['bankroll_curve_summary'])}",
    ]
    if summary["data_source_warning"]:
        summary_lines.append(f"Data warning: {summary['data_source_warning']}")
    if summary["sample_warning"]:
        summary_lines.append(f"Warning: {summary['sample_warning']}")
    message = "\n".join(summary_lines)
    return BacktestRunResult(artifacts=artifacts, summary=summary, message=message)


def load_and_prepare_feature_df(
    input_path: Path,
    dataset_scope: str,
    default_source_market: str,
) -> pd.DataFrame:
    """Load backtest features and enforce deterministic sort order."""
    feature_df = pd.read_csv(input_path)
    if "source_market" not in feature_df.columns:
        feature_df["source_market"] = None

    inferred_market = default_source_market.strip().upper() if default_source_market else _infer_default_source_market(input_path=input_path)
    feature_df["source_market"] = feature_df["source_market"].fillna(inferred_market)
    feature_df["source_market"] = feature_df["source_market"].astype(str)

    # Apply optional source-market scoping without changing CLI shape.
    normalized_scope = dataset_scope.strip().upper()
    if normalized_scope in {"HKJC", "NON_HKJC"}:
        is_hkjc = feature_df["source_market"].apply(_is_hkjc_market)
        feature_df = feature_df[is_hkjc] if normalized_scope == "HKJC" else feature_df[~is_hkjc]
    elif normalized_scope == "MIXED":
        # Explicitly keep all rows for mixed backtests.
        pass

    feature_df["kickoff_time_utc"] = pd.to_datetime(feature_df["kickoff_time_utc"], utc=True)
    sort_columns = ["kickoff_time_utc"]
    if "provider_match_id" in feature_df.columns:
        sort_columns.append("provider_match_id")
    return feature_df.sort_values(sort_columns).reset_index(drop=True)


def replay_strategy_from_predictions(
    *,
    predictions: pd.DataFrame,
    bankroll_initial: float,
    min_edge_threshold: float,
    min_confidence_threshold: float,
    max_concurrent_bets: int,
    skip_missing_data: bool,
    flip_hkjc_side: bool,
    odds_source: str,
    bankroll_policy: BankrollPolicyConfig,
    controls: RiskControlsConfig,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Replay strategy thresholds and bankroll policy over precomputed predictions."""
    trade_rows = simulate_bets(
        prediction_df=predictions,
        bankroll_initial=bankroll_initial,
        min_edge_threshold=min_edge_threshold,
        min_confidence_threshold=min_confidence_threshold,
        max_concurrent_bets=max_concurrent_bets,
        skip_missing_data=skip_missing_data,
        flip_hkjc_side=flip_hkjc_side,
        odds_source=odds_source,
        bankroll_policy=bankroll_policy,
        controls=controls,
    )
    trade_log = pd.DataFrame(trade_rows)
    summary = summarize_backtest(
        trade_log=trade_log,
        predictions=predictions,
        initial_bankroll=bankroll_initial,
    )
    return trade_log, summary


def simulate_bets(
    prediction_df: pd.DataFrame,
    bankroll_initial: float,
    min_edge_threshold: float,
    min_confidence_threshold: float,
    max_concurrent_bets: int,
    skip_missing_data: bool,
    flip_hkjc_side: bool,
    odds_source: str,
    bankroll_policy: BankrollPolicyConfig,
    controls: RiskControlsConfig,
) -> list[dict[str, object]]:
    """Simulate eligible bets under strategy thresholds, settlement rules, and bankroll controls."""
    trade_rows: list[dict[str, object]] = []
    concurrent_counts: dict[str, int] = {}
    recent_settled_returns: list[float] = []
    state = BankrollState(
        initial_bankroll=bankroll_initial,
        current_bankroll=bankroll_initial,
        peak_bankroll=bankroll_initial,
    )

    for _, row in prediction_df.iterrows():
        if should_halt_by_drawdown(state=state, controls=controls):
            break

        if skip_missing_data and int(row.get("missing_odds_flag", 0)) == 1:
            continue

        original_side = str(row.get("predicted_side", ""))
        source_market = str(row.get("source_market", ""))
        side = maybe_flip_hkjc_side(
            predicted_side=original_side,
            source_market=source_market,
            flip_hkjc_side=flip_hkjc_side,
        ) or original_side
        model_probability = float(row["model_probability"])
        confidence = float(row["confidence_score"])
        implied_probability = _select_implied_probability(row=row, side=side, odds_source=odds_source)
        odds = _select_odds(row=row, side=side, odds_source=odds_source)
        line = _select_line(row=row, odds_source=odds_source)

        if implied_probability is None or odds is None or line is None:
            continue

        entry_odds = _select_odds_for_suffix(row=row, side=side, suffix="open")
        if entry_odds is None:
            entry_odds = odds
        closing_odds = _select_odds_for_suffix(row=row, side=side, suffix="close")

        entry_line = _select_line_for_suffix(row=row, suffix="open")
        closing_line = _select_line_for_suffix(row=row, suffix="close")
        if entry_line is None:
            entry_line = line
        if closing_line is None:
            closing_line = line

        entry_implied_probability = _select_implied_probability_for_suffix(row=row, side=side, suffix="open")
        if entry_implied_probability is None:
            entry_implied_probability = _safe_implied_probability(entry_odds)

        edge = model_probability - implied_probability
        if edge < min_edge_threshold:
            continue
        if confidence < min_confidence_threshold:
            continue

        implied_probability_closing = _select_implied_probability_for_suffix(row=row, side=side, suffix="close")
        if implied_probability_closing is None:
            implied_probability_closing = _safe_implied_probability(closing_odds)

        clv_implied_edge = None
        if entry_implied_probability is not None and implied_probability_closing is not None:
            clv_implied_edge = entry_implied_probability - implied_probability_closing
        clv_pct = None
        if entry_odds is not None and closing_odds is not None and entry_odds > 0:
            # Positive CLV means bettor got a better entry price than close.
            clv_pct = (entry_odds - closing_odds) / entry_odds
        # If closing implied probability is unavailable, keep CLV proxy as NA (None) and aggregate gracefully.

        kickoff_day = str(pd.to_datetime(row["kickoff_time_utc"], utc=True).date())
        kickoff_key = str(pd.to_datetime(row["kickoff_time_utc"], utc=True))
        current_concurrent = concurrent_counts.get(kickoff_key, 0)
        if current_concurrent >= max_concurrent_bets:
            continue

        if not allows_daily_stop_loss(state=state, day_key=kickoff_day, controls=controls):
            continue

        raw_decision = compute_stake(
            current_bankroll=state.current_bankroll,
            model_probability=model_probability,
            odds=float(odds),
            config=bankroll_policy,
            recent_returns=recent_settled_returns,
        )
        decision = apply_stake_bounds(decision=raw_decision, current_bankroll=state.current_bankroll, controls=controls)
        if decision.stake_amount <= 0:
            continue
        if not allows_daily_exposure(state=state, day_key=kickoff_day, stake=decision.stake_amount, controls=controls):
            continue

        concurrent_counts[kickoff_key] = current_concurrent + 1

        bankroll_before = state.current_bankroll

        settlement = settle_handicap_bet(
            home_goals=int(row["ft_home_goals"]),
            away_goals=int(row["ft_away_goals"]),
            handicap_side=side,
            handicap_line=float(line),
            odds=float(odds),
            stake=float(decision.stake_amount),
        )
        expected_value = (
            model_probability * (float(odds) - 1.0) * float(decision.stake_amount)
            - (1.0 - model_probability) * float(decision.stake_amount)
        )
        expected_roi = expected_value / float(decision.stake_amount) if float(decision.stake_amount) > 0 else 0.0
        state.register_settlement(day_key=kickoff_day, stake=float(decision.stake_amount), pnl=float(settlement.pnl))
        recent_settled_returns.append(float(settlement.roi))

        trade_rows.append(
            {
                "provider_match_id": row.get("provider_match_id"),
                "kickoff_time_utc": row["kickoff_time_utc"],
                "competition": row.get("competition"),
                "source_market": row.get("source_market", "NON_HKJC"),
                "model_name": row.get("model_name"),
                "model_approach": row.get("model_approach"),
                "original_predicted_side": original_side,
                "effective_predicted_side": side,
                "flip_hkjc_side_enabled": bool(flip_hkjc_side),
                "side": side,
                "handicap_line": float(line),
                "odds": float(odds),
                "entry_line": float(entry_line) if entry_line is not None else None,
                "closing_line": float(closing_line) if closing_line is not None else None,
                "entry_odds": float(entry_odds) if entry_odds is not None else float(odds),
                "closing_odds": float(closing_odds) if closing_odds is not None else None,
                "model_probability": model_probability,
                "implied_probability": implied_probability,
                "entry_implied_probability": entry_implied_probability,
                "entry_implied_prob": entry_implied_probability,
                "implied_probability_closing": implied_probability_closing,
                "closing_implied_probability": implied_probability_closing,
                "closing_implied_prob": implied_probability_closing,
                "edge": edge,
                "clv_implied_edge": clv_implied_edge,
                "clv_pct": clv_pct,
                "confidence_score": confidence,
                "stake": float(decision.stake_amount),
                "stake_policy": decision.policy,
                "stake_reason": decision.reason,
                "raw_kelly_fraction": decision.raw_fraction,
                "applied_stake_fraction": decision.applied_fraction,
                "bankroll_before": bankroll_before,
                "bankroll_after": state.current_bankroll,
                "drawdown_after": state.drawdown,
                "expected_value": expected_value,
                "expected_roi": expected_roi,
                "outcome": settlement.outcome,
                "pnl": settlement.pnl,
                "roi": settlement.roi,
                "total_return": settlement.total_return,
                "components_json": json.dumps(settlement.to_dict()["components"]),
            }
        )

    return trade_rows


def write_backtest_artifacts(
    predictions: pd.DataFrame,
    trade_log: pd.DataFrame,
    summary: dict[str, object],
    output_dir: Path,
) -> BacktestArtifacts:
    """Write predictions, trade log, and summary artifacts to CSV files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.csv"
    trade_log_path = output_dir / "trade_log.csv"
    summary_path = output_dir / "summary.csv"

    if trade_log.empty and len(trade_log.columns) == 0:
        trade_log = pd.DataFrame(columns=_trade_log_columns())

    predictions.to_csv(predictions_path, index=False)
    trade_log.to_csv(trade_log_path, index=False)
    summary_row = dict(summary)
    for key in ("bankroll_curve_summary", "source_markets"):
        if key in summary_row:
            summary_row[key] = json.dumps(summary_row[key], ensure_ascii=False)
    pd.DataFrame([summary_row]).to_csv(summary_path, index=False)

    return BacktestArtifacts(
        predictions_path=predictions_path,
        trade_log_path=trade_log_path,
        summary_path=summary_path,
    )


def _resolve_backtest_output_dir(output_dir: Path, run_id: str | None) -> Path:
    normalized_run_id = (run_id or "").strip()
    if not normalized_run_id:
        return output_dir
    return output_dir / normalized_run_id


def _trade_log_columns() -> list[str]:
    return [
        "provider_match_id",
        "kickoff_time_utc",
        "competition",
        "source_market",
        "model_name",
        "model_approach",
        "original_predicted_side",
        "effective_predicted_side",
        "flip_hkjc_side_enabled",
        "side",
        "handicap_line",
        "odds",
        "entry_line",
        "closing_line",
        "entry_odds",
        "closing_odds",
        "model_probability",
        "implied_probability",
        "entry_implied_probability",
        "implied_probability_closing",
        "closing_implied_probability",
        "edge",
        "clv_implied_edge",
        "clv_pct",
        "confidence_score",
        "stake",
        "stake_policy",
        "stake_reason",
        "raw_kelly_fraction",
        "applied_stake_fraction",
        "bankroll_before",
        "bankroll_after",
        "drawdown_after",
        "expected_value",
        "expected_roi",
        "outcome",
        "pnl",
        "roi",
        "total_return",
        "components_json",
    ]


def _select_line(row: pd.Series, odds_source: str) -> float | None:
    if odds_source == "closing":
        line = row.get("handicap_close_line")
    else:
        line = row.get("handicap_open_line")
    if pd.isna(line):
        return None
    return float(line)


def _select_line_for_suffix(row: pd.Series, suffix: str) -> float | None:
    column = f"handicap_{suffix}_line"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _select_odds(row: pd.Series, side: str, odds_source: str) -> float | None:
    suffix = "close" if odds_source == "closing" else "open"
    column = f"odds_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _select_implied_probability(row: pd.Series, side: str, odds_source: str) -> float | None:
    suffix = "close" if odds_source == "closing" else "open"
    column = f"implied_prob_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _select_implied_probability_for_suffix(row: pd.Series, side: str, suffix: str) -> float | None:
    column = f"implied_prob_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _select_odds_for_suffix(row: pd.Series, side: str, suffix: str) -> float | None:
    column = f"odds_{side}_{suffix}"
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _safe_implied_probability(odds: float | None) -> float | None:
    if odds is None or odds <= 0:
        return None
    return float(1.0 / odds)


def _infer_default_source_market(input_path: Path) -> str:
    normalized = str(input_path).lower()
    if "hkjc" in normalized:
        return "HKJC"
    return "NON_HKJC"


def _is_hkjc_market(value: object) -> bool:
    normalized = str(value).strip().upper()
    return "HKJC" in normalized


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_optional_float(value: Any, default: float | None) -> float | None:
    if value is None:
        return default
    return float(value)


def _as_policy_name(value: Any, default: str) -> StakePolicyName:
    candidate = str(value) if value is not None else default
    if candidate not in {"flat", "fixed_fraction", "fractional_kelly", "vol_target"}:
        raise ValueError(f"Unsupported bankroll policy: {candidate}")
    return cast(StakePolicyName, candidate)
