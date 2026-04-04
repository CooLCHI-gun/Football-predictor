from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

from src.strategy.settlement import settle_handicap_bet


def required_reporting_metrics() -> list[str]:
    """Return mandatory summary fields required in backtest reporting."""
    return [
        "total_matches_evaluated",
        "total_bets_placed",
        "wins",
        "losses",
        "pushes",
        "half_wins",
        "half_losses",
        "win_rate",
        "roi",
        "total_stake",
        "total_return",
        "net_profit",
        "max_drawdown",
        "bankroll_curve_summary",
        "risk_of_ruin_estimate",
        "total_ev",
        "avg_ev_per_bet",
        "avg_clv_implied_edge",
        "avg_clv_pct",
        "median_clv_pct",
        "pct_positive_clv",
        "clv_score",
        "clv_observations",
        "clv_observation_rate",
        "clv_data_warning",
    ]


def summarize_backtest(
    trade_log: pd.DataFrame,
    predictions: pd.DataFrame,
    initial_bankroll: float,
) -> dict[str, Any]:
    """Aggregate trade and prediction outputs into reporting metrics for summary.csv."""
    wins = int((trade_log["outcome"] == "win").sum()) if not trade_log.empty else 0
    losses = int((trade_log["outcome"] == "lose").sum()) if not trade_log.empty else 0
    pushes = int((trade_log["outcome"] == "push").sum()) if not trade_log.empty else 0
    half_wins = int((trade_log["outcome"] == "half-win").sum()) if not trade_log.empty else 0
    half_losses = int((trade_log["outcome"] == "half-lose").sum()) if not trade_log.empty else 0

    total_bets_placed = int(len(trade_log))
    total_matches_evaluated = int(len(predictions))
    total_stake = float(trade_log["stake"].sum()) if not trade_log.empty else 0.0
    total_return = float(trade_log["total_return"].sum()) if not trade_log.empty else 0.0
    net_profit = total_return - total_stake
    total_settled_bets = wins + losses + pushes + half_wins + half_losses
    win_rate = (wins / total_settled_bets) if total_settled_bets else 0.0
    roi = (net_profit / total_stake) if total_stake else 0.0

    total_ev = float(trade_log["expected_value"].sum()) if "expected_value" in trade_log.columns and not trade_log.empty else 0.0
    avg_ev_per_bet = (total_ev / total_bets_placed) if total_bets_placed else 0.0
    avg_clv_implied_edge = None
    if "clv_implied_edge" in trade_log.columns and not trade_log.empty:
        clv_series = pd.to_numeric(trade_log["clv_implied_edge"], errors="coerce").dropna()
        if not clv_series.empty:
            avg_clv_implied_edge = float(clv_series.mean())

    clv_metrics = summarize_clv_metrics(trade_log)
    avg_clv_pct = clv_metrics["avg_clv_pct"]
    median_clv_pct = clv_metrics["median_clv_pct"]
    pct_positive_clv = clv_metrics["pct_positive_clv"]
    clv_score = compute_clv_score(avg_clv_pct=avg_clv_pct, pct_positive_clv=pct_positive_clv)
    clv_observations = int(clv_metrics["clv_observations"] or 0)
    clv_observation_rate = (
        float(clv_observations / total_bets_placed) if total_bets_placed > 0 else 0.0
    )
    clv_data_warning = derive_clv_data_warning(
        total_bets_placed=total_bets_placed,
        clv_observations=clv_observations,
        has_distinct_price_points=bool(clv_metrics["has_distinct_price_points"]),
    )

    risk_of_ruin_estimate = estimate_risk_of_ruin(
        trade_log=trade_log,
        initial_bankroll=initial_bankroll,
    )

    bankroll_curve = initial_bankroll + trade_log["pnl"].cumsum() if not trade_log.empty else pd.Series(dtype=float)
    max_drawdown = compute_max_drawdown(bankroll_curve) if not trade_log.empty else 0.0

    bankroll_curve_summary = {
        "initial_bankroll": initial_bankroll,
        "ending_bankroll": float(bankroll_curve.iloc[-1]) if not trade_log.empty else initial_bankroll,
        "peak_bankroll": float(bankroll_curve.cummax().max()) if not trade_log.empty else initial_bankroll,
        "min_bankroll": float(bankroll_curve.min()) if not trade_log.empty else initial_bankroll,
    }

    brier_score = None
    log_loss_value = None
    if not predictions.empty and "home_cover_probability" in predictions.columns:
        scoring_df = predictions.copy()
        scoring_df["actual_home_cover"] = scoring_df.apply(_actual_home_cover_label_or_none, axis=1)
        scoring_df["home_cover_probability"] = pd.to_numeric(scoring_df["home_cover_probability"], errors="coerce")
        scoring_df = scoring_df.dropna(subset=["actual_home_cover", "home_cover_probability"])
        if not scoring_df.empty:
            actual_home_cover = scoring_df["actual_home_cover"].astype(int)
            probabilities = scoring_df["home_cover_probability"].astype(float)
            brier_score = float(brier_score_loss(actual_home_cover, probabilities))
            log_loss_value = float(log_loss(actual_home_cover, probabilities, labels=[0, 1]))

    source_markets = sorted({str(value) for value in predictions.get("source_market", pd.Series(dtype=str)).dropna().tolist()})
    data_source_warning = None
    if any(market != "HKJC" for market in source_markets):
        data_source_warning = (
            "Backtest includes non-HKJC historical market data. Results may not match HKJC pricing or execution conditions."
        )

    sample_warning = None
    if total_matches_evaluated < 100:
        sample_warning = (
            "MVP validation threshold not met: fewer than 100 matches evaluated. "
            "This is below the minimum MVP threshold and far below robustness evidence."
        )

    return {
        "total_matches_evaluated": total_matches_evaluated,
        "total_bets_placed": total_bets_placed,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "half_wins": half_wins,
        "half_losses": half_losses,
        "win_rate": win_rate,
        "roi": roi,
        "total_stake": total_stake,
        "total_return": total_return,
        "net_profit": net_profit,
        "max_drawdown": max_drawdown,
        "bankroll_curve_summary": bankroll_curve_summary,
        "risk_of_ruin_estimate": risk_of_ruin_estimate,
        "total_ev": total_ev,
        "avg_ev_per_bet": avg_ev_per_bet,
        "avg_clv_implied_edge": avg_clv_implied_edge,
        "avg_clv_pct": avg_clv_pct,
        "median_clv_pct": median_clv_pct,
        "pct_positive_clv": pct_positive_clv,
        "clv_score": clv_score,
        "clv_observations": clv_observations,
        "clv_observation_rate": clv_observation_rate,
        "clv_data_warning": clv_data_warning,
        "brier_score": brier_score,
        "log_loss": log_loss_value,
        "source_markets": source_markets,
        "data_source_warning": data_source_warning,
        "sample_warning": sample_warning,
    }


def summarize_clv_metrics(trade_log: pd.DataFrame) -> dict[str, float | bool | None]:
    if trade_log.empty or "clv_pct" not in trade_log.columns:
        return {
            "avg_clv_pct": None,
            "median_clv_pct": None,
            "pct_positive_clv": None,
            "clv_observations": 0,
            "has_distinct_price_points": False,
        }

    clv_pct_series = pd.to_numeric(trade_log["clv_pct"], errors="coerce").dropna()
    has_distinct_price_points = False
    if {"entry_odds", "closing_odds"}.issubset(trade_log.columns):
        entry_series = pd.to_numeric(trade_log["entry_odds"], errors="coerce")
        close_series = pd.to_numeric(trade_log["closing_odds"], errors="coerce")
        comparable = pd.concat([entry_series, close_series], axis=1).dropna()
        if not comparable.empty:
            has_distinct_price_points = bool((comparable.iloc[:, 0] != comparable.iloc[:, 1]).any())

    if clv_pct_series.empty:
        return {
            "avg_clv_pct": None,
            "median_clv_pct": None,
            "pct_positive_clv": None,
            "clv_observations": 0,
            "has_distinct_price_points": has_distinct_price_points,
        }
    return {
        "avg_clv_pct": float(clv_pct_series.mean()),
        "median_clv_pct": float(clv_pct_series.median()),
        "pct_positive_clv": float((clv_pct_series > 0).mean()),
        "clv_observations": int(clv_pct_series.size),
        "has_distinct_price_points": has_distinct_price_points,
    }


def derive_clv_data_warning(
    *,
    total_bets_placed: int,
    clv_observations: int,
    has_distinct_price_points: bool,
) -> str | None:
    if total_bets_placed <= 0:
        return None
    if clv_observations <= 0:
        return (
            "CLV unavailable: missing entry/closing price pairs in historical dataset. "
            "For HKJC CLV analysis, provide both open(entry) and close odds/lines."
        )
    if not has_distinct_price_points:
        return (
            "CLV weak signal: entry and closing prices are effectively identical across observed bets. "
            "Verify HKJC historical open/close capture pipeline."
        )
    return None


def compute_max_drawdown(bankroll_curve: pd.Series) -> float:
    """Compute peak-to-trough drawdown ratio from a bankroll curve series."""
    if bankroll_curve.empty:
        return 0.0
    running_peak = bankroll_curve.cummax()
    drawdown = (running_peak - bankroll_curve) / running_peak.replace(0, pd.NA)
    return float(drawdown.fillna(0.0).max())


def estimate_risk_of_ruin(trade_log: pd.DataFrame, initial_bankroll: float) -> float | None:
    """Estimate risk of ruin with a transparent classical approximation.

    Assumptions and limits:
    - Independent bets with roughly stable edge and stake size.
    - Uses average model win probability, average decimal odds, and average stake from the observed trade log.
    - This is an approximation for research guidance, not a guarantee.
    """
    if trade_log.empty:
        return None

    required_columns = {"model_probability", "odds", "stake"}
    if not required_columns.issubset(trade_log.columns):
        return None

    p_series = pd.to_numeric(trade_log["model_probability"], errors="coerce").dropna()
    odds_series = pd.to_numeric(trade_log["odds"], errors="coerce").dropna()
    stake_series = pd.to_numeric(trade_log["stake"], errors="coerce").dropna()

    if p_series.empty or odds_series.empty or stake_series.empty:
        return None

    avg_p = float(p_series.mean())
    avg_odds = float(odds_series.mean())
    avg_stake = float(stake_series.mean())

    if initial_bankroll <= 0 or avg_stake <= 0 or avg_odds <= 1.0:
        return None

    p = float(np.clip(avg_p, 1e-6, 1.0 - 1e-6))
    q = 1.0 - p
    payout_ratio = avg_odds - 1.0

    effective_win_term = p * payout_ratio
    if effective_win_term <= q:
        return 1.0

    bankroll_units = initial_bankroll / avg_stake
    if bankroll_units <= 0:
        return None

    ror = (q / effective_win_term) ** bankroll_units
    return float(np.clip(ror, 0.0, 1.0))


def compute_clv_score(*, avg_clv_pct: float | None, pct_positive_clv: float | None) -> float | None:
    if avg_clv_pct is None or pct_positive_clv is None:
        return None
    return float(0.6 * avg_clv_pct + 0.4 * (pct_positive_clv - 0.5))


def _actual_home_cover_label_or_none(row: pd.Series) -> int | None:
    """Convert match result into binary home-cover label; return None when required fields are missing."""
    line = _pick_numeric(row, ["handicap_close_line", "handicap_open_line"])
    odds = _pick_numeric(row, ["odds_home_close", "odds_home_open"])
    home_goals = _pick_int(row.get("ft_home_goals"))
    away_goals = _pick_int(row.get("ft_away_goals"))

    if line is None or odds is None or home_goals is None or away_goals is None or odds <= 1.0:
        return None

    result = settle_handicap_bet(
        home_goals=home_goals,
        away_goals=away_goals,
        handicap_side="home",
        handicap_line=line,
        odds=odds,
        stake=1.0,
    )
    return int(result.pnl > 0)


def _pick_numeric(row: pd.Series, columns: list[str]) -> float | None:
    for column in columns:
        if column not in row.index:
            continue
        value = row.get(column)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if pd.isna(parsed):
            continue
        return float(parsed)
    return None


def _pick_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed
