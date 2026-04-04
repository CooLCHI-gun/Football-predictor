from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.features.hk_market_compare import add_hk_vs_consensus_features
from src.strategy.settlement import settle_handicap_bet

REQUIRED_COLUMNS = {
    "kickoff_time_utc",
    "competition",
    "home_team_name",
    "away_team_name",
    "ft_home_goals",
    "ft_away_goals",
}

COLUMN_ALIASES = {
    "date": "kickoff_time_utc",
    "kickoff_time": "kickoff_time_utc",
    "league": "competition",
    "home_team": "home_team_name",
    "away_team": "away_team_name",
    "home_goals": "ft_home_goals",
    "away_goals": "ft_away_goals",
    "handicap_open": "handicap_open_line",
    "handicap_close": "handicap_close_line",
}


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamMatchState:
    kickoff_time_utc: pd.Timestamp
    points: int
    goals_for: int
    goals_against: int
    is_home: bool
    goal_diff: int
    hdc_cover_score: float | None = None
    xg_diff: float | None = None


def build_feature_pipeline(
    input_path: Path = Path("data/raw/matches_template.csv"),
    output_path: Path = Path("data/processed/features_mvp.csv"),
) -> str:
    """Build MVP features with strict as-of logic only.

    Features are computed from each team's history available strictly before the
    current kickoff timestamp. Full-time labels are never included in pre-match features.
    """
    LOGGER.info("Feature build started: input=%s output=%s", input_path, output_path)
    raw_df = load_raw_matches(input_path)
    normalized_df = normalize_schema(raw_df)
    sorted_df = sort_chronologically(normalized_df)
    feature_df = compute_features(sorted_df)
    write_features(feature_df, output_path)
    LOGGER.info("Feature build completed: rows=%s output=%s", len(feature_df), output_path)
    return f"Feature build complete: {len(feature_df)} rows -> {output_path}"


def load_raw_matches(input_path: Path) -> pd.DataFrame:
    """Load raw match CSV from disk."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    return pd.read_csv(input_path)


def normalize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize input columns into canonical schema used by the feature pipeline."""
    normalized = df.rename(columns=COLUMN_ALIASES).copy()

    missing_required = [col for col in REQUIRED_COLUMNS if col not in normalized.columns]
    if missing_required:
        missing_str = ", ".join(sorted(missing_required))
        raise ValueError(f"Missing required columns: {missing_str}")

    normalized["kickoff_time_utc"] = pd.to_datetime(normalized["kickoff_time_utc"], utc=True)
    normalized["ft_home_goals"] = pd.to_numeric(normalized["ft_home_goals"], errors="coerce")
    normalized["ft_away_goals"] = pd.to_numeric(normalized["ft_away_goals"], errors="coerce")
    normalized["provider_match_id"] = normalized.get("provider_match_id", pd.Series(dtype="object"))

    return normalized


def sort_chronologically(df: pd.DataFrame) -> pd.DataFrame:
    sort_keys = ["kickoff_time_utc"]
    if "provider_match_id" in df.columns:
        sort_keys.append("provider_match_id")
    return df.sort_values(sort_keys).reset_index(drop=True)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute chronological as-of features without look-ahead leakage."""
    team_history: dict[str, list[TeamMatchState]] = defaultdict(list)
    h2h_history: dict[tuple[str, str], deque[dict[str, object]]] = defaultdict(lambda: deque(maxlen=20))
    team_last_kickoff: dict[str, pd.Timestamp] = {}
    team_elo: dict[str, float] = defaultdict(lambda: 1500.0)

    rows: list[dict[str, object]] = []

    for _, row in df.iterrows():
        kickoff = row["kickoff_time_utc"]
        home_team = str(row["home_team_name"])
        away_team = str(row["away_team_name"])

        home_hist = team_history[home_team]
        away_hist = team_history[away_team]

        home_hdc_scores = [item.hdc_cover_score for item in home_hist if item.hdc_cover_score is not None]
        away_hdc_scores = [item.hdc_cover_score for item in away_hist if item.hdc_cover_score is not None]

        home_goal_diff_hist = [item.goal_diff for item in home_hist]
        away_goal_diff_hist = [item.goal_diff for item in away_hist]

        home_xg_diff_hist = [item.xg_diff for item in home_hist if item.xg_diff is not None]
        away_xg_diff_hist = [item.xg_diff for item in away_hist if item.xg_diff is not None]

        home_points_5 = _sum_last_n([m.points for m in home_hist], 5)
        home_points_10 = _sum_last_n([m.points for m in home_hist], 10)
        away_points_5 = _sum_last_n([m.points for m in away_hist], 5)
        away_points_10 = _sum_last_n([m.points for m in away_hist], 10)

        home_only_hist = [m for m in home_hist if m.is_home]
        away_only_hist = [m for m in away_hist if not m.is_home]

        home_form_home_5 = _sum_last_n([m.points for m in home_only_hist], 5)
        away_form_away_5 = _sum_last_n([m.points for m in away_only_hist], 5)

        home_gf_5 = _sum_last_n([m.goals_for for m in home_hist], 5)
        home_ga_5 = _sum_last_n([m.goals_against for m in home_hist], 5)
        away_gf_5 = _sum_last_n([m.goals_for for m in away_hist], 5)
        away_ga_5 = _sum_last_n([m.goals_against for m in away_hist], 5)

        rest_days_home = _rest_days(team_last_kickoff.get(home_team), kickoff)
        rest_days_away = _rest_days(team_last_kickoff.get(away_team), kickoff)
        rest_days_diff = _subtract_optional(rest_days_home, rest_days_away)

        home_recent5_rest_days_mean = _mean_recent_rest_days(home_hist, 5)
        away_recent5_rest_days_mean = _mean_recent_rest_days(away_hist, 5)
        recent5_rest_days_diff = _subtract_optional(home_recent5_rest_days_mean, away_recent5_rest_days_mean)

        recent5_hdc_cover_rate_home = _cover_rate_with_shrinkage(home_hdc_scores, n=5)
        recent10_hdc_cover_rate_home = _cover_rate_with_shrinkage(home_hdc_scores, n=10)
        recent5_hdc_cover_rate_away = _cover_rate_with_shrinkage(away_hdc_scores, n=5)
        recent10_hdc_cover_rate_away = _cover_rate_with_shrinkage(away_hdc_scores, n=10)

        recent5_goal_diff_mean_home = _mean_last_n(home_goal_diff_hist, n=5)
        recent10_goal_diff_mean_home = _mean_last_n(home_goal_diff_hist, n=10)
        recent5_goal_diff_mean_away = _mean_last_n(away_goal_diff_hist, n=5)
        recent10_goal_diff_mean_away = _mean_last_n(away_goal_diff_hist, n=10)

        recent5_xg_diff_mean_home = _mean_last_n(home_xg_diff_hist, n=5)
        recent5_xg_diff_mean_away = _mean_last_n(away_xg_diff_hist, n=5)
        recent10_xg_diff_mean_home = _mean_last_n(home_xg_diff_hist, n=10)
        recent10_xg_diff_mean_away = _mean_last_n(away_xg_diff_hist, n=10)

        recent_hdc_cover_ewm_alpha_0p3_home = _ewm_recent(home_hdc_scores, alpha=0.3)
        recent_hdc_cover_ewm_alpha_0p3_away = _ewm_recent(away_hdc_scores, alpha=0.3)

        recent5_hdc_cover_advantage = _subtract_optional(recent5_hdc_cover_rate_home, recent5_hdc_cover_rate_away)
        recent10_hdc_cover_advantage = _subtract_optional(recent10_hdc_cover_rate_home, recent10_hdc_cover_rate_away)

        h2h_stats = _compute_h2h_stats(
            h2h_records=list(h2h_history[_h2h_key(home_team, away_team)]),
            home_team=home_team,
            away_team=away_team,
        )

        home_elo_pre = team_elo[home_team]
        away_elo_pre = team_elo[away_team]

        feature_row = {
            "provider_match_id": row.get("provider_match_id", None),
            "source_market": row.get("source_market", "NON_HKJC"),
            "kickoff_time_utc": kickoff,
            "competition": row["competition"],
            "home_team_name": home_team,
            "away_team_name": away_team,
            "ft_home_goals": row["ft_home_goals"],
            "ft_away_goals": row["ft_away_goals"],
            "target_handicap_side": str(row.get("handicap_side", "home")).strip().lower() or "home",
            "home_form_points_last5": home_points_5,
            "home_form_points_last10": home_points_10,
            "away_form_points_last5": away_points_5,
            "away_form_points_last10": away_points_10,
            "home_recent_home_form_last5": home_form_home_5,
            "away_recent_away_form_last5": away_form_away_5,
            "home_goals_scored_last5": home_gf_5,
            "home_goals_conceded_last5": home_ga_5,
            "home_goal_diff_last5": home_gf_5 - home_ga_5,
            "away_goals_scored_last5": away_gf_5,
            "away_goals_conceded_last5": away_ga_5,
            "away_goal_diff_last5": away_gf_5 - away_ga_5,
            "rest_days_home": rest_days_home,
            "rest_days_away": rest_days_away,
            "rest_days_diff": rest_days_diff,
            "recent5_rest_days_diff": recent5_rest_days_diff,
            "recent5_hdc_cover_rate_home": recent5_hdc_cover_rate_home,
            "recent10_hdc_cover_rate_home": recent10_hdc_cover_rate_home,
            "recent5_hdc_cover_rate_away": recent5_hdc_cover_rate_away,
            "recent10_hdc_cover_rate_away": recent10_hdc_cover_rate_away,
            "recent5_goal_diff_mean_home": recent5_goal_diff_mean_home,
            "recent10_goal_diff_mean_home": recent10_goal_diff_mean_home,
            "recent5_goal_diff_mean_away": recent5_goal_diff_mean_away,
            "recent10_goal_diff_mean_away": recent10_goal_diff_mean_away,
            "recent5_xg_diff_mean_home": recent5_xg_diff_mean_home,
            "recent5_xg_diff_mean_away": recent5_xg_diff_mean_away,
            "recent10_xg_diff_mean_home": recent10_xg_diff_mean_home,
            "recent10_xg_diff_mean_away": recent10_xg_diff_mean_away,
            "recent_hdc_cover_ewm_alpha_0p3_home": recent_hdc_cover_ewm_alpha_0p3_home,
            "recent_hdc_cover_ewm_alpha_0p3_away": recent_hdc_cover_ewm_alpha_0p3_away,
            "recent5_hdc_cover_advantage": recent5_hdc_cover_advantage,
            "recent10_hdc_cover_advantage": recent10_hdc_cover_advantage,
            "h2h_last5_hdc_cover_rate": h2h_stats["h2h_last5_hdc_cover_rate"],
            "h2h_last10_hdc_cover_rate": h2h_stats["h2h_last10_hdc_cover_rate"],
            "h2h_home_last5_hdc_cover_rate": h2h_stats["h2h_home_last5_hdc_cover_rate"],
            "h2h_last5_hdc_cover_mean": h2h_stats["h2h_last5_hdc_cover_mean"],
            "h2h_last10_hdc_cover_mean": h2h_stats["h2h_last10_hdc_cover_mean"],
            "h2h_last5_goal_diff_mean": h2h_stats["h2h_last5_goal_diff_mean"],
            "h2h_last5_xg_diff_mean": h2h_stats["h2h_last5_xg_diff_mean"],
            "h2h_sample_size_last5": h2h_stats["h2h_sample_size_last5"],
            "h2h_sample_size_last10": h2h_stats["h2h_sample_size_last10"],
            "elo_home_pre": home_elo_pre,
            "elo_away_pre": away_elo_pre,
            "elo_diff_pre": home_elo_pre - away_elo_pre,
            "history_home_matches_count": len(home_hist),
            "history_away_matches_count": len(away_hist),
            "missing_home_history_flag": int(len(home_hist) == 0),
            "missing_away_history_flag": int(len(away_hist) == 0),
            "missing_home_ft_goals_flag": int(pd.isna(row["ft_home_goals"])),
            "missing_away_ft_goals_flag": int(pd.isna(row["ft_away_goals"])),
        }

        feature_row.update(_odds_and_line_features(row))
        rows.append(feature_row)

        home_goals = int(row["ft_home_goals"]) if not pd.isna(row["ft_home_goals"]) else 0
        away_goals = int(row["ft_away_goals"]) if not pd.isna(row["ft_away_goals"]) else 0

        home_hdc_cover_score = _compute_home_hdc_cover_score(row=row, home_goals=home_goals, away_goals=away_goals)
        away_hdc_cover_score = -home_hdc_cover_score if home_hdc_cover_score is not None else None
        xg_diff = _compute_optional_xg_diff(row)

        home_points, away_points = _match_points(home_goals, away_goals)

        team_history[home_team].append(
            TeamMatchState(
                kickoff_time_utc=kickoff,
                points=home_points,
                goals_for=home_goals,
                goals_against=away_goals,
                is_home=True,
                goal_diff=home_goals - away_goals,
                hdc_cover_score=home_hdc_cover_score,
                xg_diff=xg_diff,
            )
        )
        team_history[away_team].append(
            TeamMatchState(
                kickoff_time_utc=kickoff,
                points=away_points,
                goals_for=away_goals,
                goals_against=home_goals,
                is_home=False,
                goal_diff=away_goals - home_goals,
                hdc_cover_score=away_hdc_cover_score,
                xg_diff=-xg_diff if xg_diff is not None else None,
            )
        )

        h2h_history[_h2h_key(home_team, away_team)].append(
            {
                "home_team": home_team,
                "away_team": away_team,
                "goal_diff": float(home_goals - away_goals),
                "xg_diff": xg_diff,
                "home_hdc_cover_score": home_hdc_cover_score,
            }
        )

        team_last_kickoff[home_team] = kickoff
        team_last_kickoff[away_team] = kickoff

        updated_home_elo, updated_away_elo = _update_elo(
            home_elo=home_elo_pre,
            away_elo=away_elo_pre,
            home_goals=home_goals,
            away_goals=away_goals,
            k_factor=20.0,
        )
        team_elo[home_team] = updated_home_elo
        team_elo[away_team] = updated_away_elo

    feature_df = pd.DataFrame(rows)
    return add_hk_vs_consensus_features(feature_df)


def write_features(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def _sum_last_n(values: list[int], n: int) -> int:
    return int(sum(values[-n:]))


def _mean_last_n(values: list[float], n: int) -> float | None:
    if not values:
        return None
    window = values[-n:]
    if not window:
        return None
    return float(np.mean(window))


def _rest_days(last_kickoff: pd.Timestamp | None, current_kickoff: pd.Timestamp) -> float | None:
    if last_kickoff is None:
        return None
    delta = current_kickoff - last_kickoff
    return round(delta.total_seconds() / 86400.0, 4)


def _match_points(home_goals: int, away_goals: int) -> tuple[int, int]:
    if home_goals > away_goals:
        return 3, 0
    if home_goals < away_goals:
        return 0, 3
    return 1, 1


def _compute_home_hdc_cover_score(row: pd.Series, home_goals: int, away_goals: int) -> float | None:
    line = row.get("handicap_close_line")
    odds = row.get("odds_home_close")
    if pd.isna(line) or pd.isna(odds):
        return None
    try:
        settlement = settle_handicap_bet(
            home_goals=home_goals,
            away_goals=away_goals,
            handicap_side="home",
            handicap_line=float(line),
            odds=float(odds),
            stake=1.0,
        )
    except Exception:
        return None
    return _encode_outcome_to_score(settlement.outcome)


def _encode_outcome_to_score(outcome: str) -> float:
    mapping = {
        "win": 1.0,
        "half-win": 0.5,
        "push": 0.0,
        "half-lose": -0.5,
        "lose": -1.0,
    }
    return float(mapping.get(outcome, 0.0))


def _compute_optional_xg_diff(row: pd.Series) -> float | None:
    home_xg = _to_float(row.get("home_xg", row.get("xg_home", np.nan)))
    away_xg = _to_float(row.get("away_xg", row.get("xg_away", np.nan)))
    if pd.isna(home_xg) or pd.isna(away_xg):
        return None
    return float(home_xg - away_xg)


def _cover_rate_with_shrinkage(scores: list[float], n: int, prior: float = 0.5, k: float = 12.0) -> float | None:
    if not scores:
        return None
    window = scores[-n:]
    if not window:
        return None
    raw_rate = float(np.mean([1.0 if value > 0 else 0.0 for value in window]))
    shrunk = (len(window) * raw_rate + k * prior) / (len(window) + k)
    return float(np.clip(shrunk, 0.0, 1.0))


def _ewm_recent(values: list[float], alpha: float) -> float | None:
    if not values:
        return None
    series = pd.Series(values, dtype=float)
    return float(series.ewm(alpha=alpha, adjust=False).mean().iloc[-1])


def _mean_recent_rest_days(history: list[TeamMatchState], n: int) -> float | None:
    if len(history) < 2:
        return None
    recent = history[-n:]
    if len(recent) < 2:
        return None
    rest_days: list[float] = []
    for previous, current in zip(recent[:-1], recent[1:], strict=False):
        delta_days = (current.kickoff_time_utc - previous.kickoff_time_utc).total_seconds() / 86400.0
        rest_days.append(delta_days)
    if not rest_days:
        return None
    return float(np.mean(rest_days))


def _subtract_optional(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left - right)


def _h2h_key(home_team: str, away_team: str) -> tuple[str, str]:
    return tuple(sorted([home_team, away_team]))


def _compute_h2h_stats(
    *,
    h2h_records: list[dict[str, object]],
    home_team: str,
    away_team: str,
) -> dict[str, float | int | None]:
    oriented_cover_scores: list[float] = []
    oriented_goal_diff: list[float] = []
    oriented_xg_diff: list[float] = []
    home_fixture_cover_scores: list[float] = []

    for record in h2h_records:
        record_home = str(record.get("home_team", ""))
        record_away = str(record.get("away_team", ""))
        record_cover = record.get("home_hdc_cover_score")
        record_goal_diff = record.get("goal_diff")
        record_xg_diff = record.get("xg_diff")

        if record_home == home_team and record_away == away_team:
            cover_score = _as_optional_float(record_cover)
            goal_diff = _as_optional_float(record_goal_diff)
            xg_diff = _as_optional_float(record_xg_diff)
            if cover_score is not None:
                oriented_cover_scores.append(cover_score)
                home_fixture_cover_scores.append(cover_score)
            if goal_diff is not None:
                oriented_goal_diff.append(goal_diff)
            if xg_diff is not None:
                oriented_xg_diff.append(xg_diff)
            continue

        if record_home == away_team and record_away == home_team:
            cover_score = _as_optional_float(record_cover)
            goal_diff = _as_optional_float(record_goal_diff)
            xg_diff = _as_optional_float(record_xg_diff)
            if cover_score is not None:
                oriented_cover_scores.append(-cover_score)
            if goal_diff is not None:
                oriented_goal_diff.append(-goal_diff)
            if xg_diff is not None:
                oriented_xg_diff.append(-xg_diff)

    h2h_last5_scores = oriented_cover_scores[-5:]
    h2h_last10_scores = oriented_cover_scores[-10:]
    h2h_home_last5_scores = home_fixture_cover_scores[-5:]

    return {
        "h2h_last5_hdc_cover_rate": _cover_rate_with_shrinkage(h2h_last5_scores, n=5, prior=0.5, k=16.0),
        "h2h_last10_hdc_cover_rate": _cover_rate_with_shrinkage(h2h_last10_scores, n=10, prior=0.5, k=16.0),
        "h2h_home_last5_hdc_cover_rate": _cover_rate_with_shrinkage(h2h_home_last5_scores, n=5, prior=0.5, k=16.0),
        "h2h_last5_hdc_cover_mean": _clip_optional(_mean_last_n(h2h_last5_scores, n=5), -1.0, 1.0),
        "h2h_last10_hdc_cover_mean": _clip_optional(_mean_last_n(h2h_last10_scores, n=10), -1.0, 1.0),
        "h2h_last5_goal_diff_mean": _clip_optional(_mean_last_n(oriented_goal_diff, n=5), -3.0, 3.0),
        "h2h_last5_xg_diff_mean": _clip_optional(_mean_last_n(oriented_xg_diff, n=5), -2.5, 2.5),
        "h2h_sample_size_last5": int(len(h2h_last5_scores)),
        "h2h_sample_size_last10": int(len(h2h_last10_scores)),
    }


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _clip_optional(value: float | None, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    return float(np.clip(value, lower, upper))


def _update_elo(
    home_elo: float,
    away_elo: float,
    home_goals: int,
    away_goals: int,
    k_factor: float,
) -> tuple[float, float]:
    expected_home = 1.0 / (1.0 + 10 ** ((away_elo - home_elo) / 400.0))
    expected_away = 1.0 - expected_home

    if home_goals > away_goals:
        score_home, score_away = 1.0, 0.0
    elif home_goals < away_goals:
        score_home, score_away = 0.0, 1.0
    else:
        score_home, score_away = 0.5, 0.5

    updated_home = home_elo + k_factor * (score_home - expected_home)
    updated_away = away_elo + k_factor * (score_away - expected_away)
    return updated_home, updated_away


def _odds_and_line_features(row: pd.Series) -> dict[str, object]:
    open_line = _to_float(row.get("handicap_open_line", np.nan))
    close_line = _to_float(row.get("handicap_close_line", np.nan))

    home_open_odds = _to_float(row.get("odds_home_open", np.nan))
    away_open_odds = _to_float(row.get("odds_away_open", np.nan))
    home_close_odds = _to_float(row.get("odds_home_close", np.nan))
    away_close_odds = _to_float(row.get("odds_away_close", np.nan))

    open_probs = _normalized_implied_probs(home_open_odds, away_open_odds)
    close_probs = _normalized_implied_probs(home_close_odds, away_close_odds)

    return {
        "handicap_open_line": open_line,
        "handicap_close_line": close_line,
        "handicap_line_movement": None if pd.isna(open_line) or pd.isna(close_line) else close_line - open_line,
        "missing_handicap_line_flag": int(pd.isna(open_line) or pd.isna(close_line)),
        "odds_home_open": home_open_odds,
        "odds_away_open": away_open_odds,
        "odds_home_close": home_close_odds,
        "odds_away_close": away_close_odds,
        "implied_prob_home_open": open_probs[0],
        "implied_prob_away_open": open_probs[1],
        "implied_prob_home_close": close_probs[0],
        "implied_prob_away_close": close_probs[1],
        "missing_odds_flag": int(any(pd.isna(v) for v in [home_close_odds, away_close_odds])),
    }


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _normalized_implied_probs(home_odds: float, away_odds: float) -> tuple[float | None, float | None]:
    if pd.isna(home_odds) or pd.isna(away_odds) or home_odds <= 1.0 or away_odds <= 1.0:
        return None, None

    raw_home = 1.0 / home_odds
    raw_away = 1.0 / away_odds
    total = raw_home + raw_away
    if total <= 0:
        return None, None
    return raw_home / total, raw_away / total
