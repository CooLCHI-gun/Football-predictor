from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.adapters.hkjc.default_adapter import DefaultHKJCAdapter
from src.features.pipeline import build_feature_pipeline
from src.live_feed.providers.hkjc_provider import HKJCFootballProvider
from src.live_feed.providers.hkjc_result_validator import extract_full_time_score


@dataclass(frozen=True)
class HKJCHistoryCollectionSummary:
    start_date: str
    end_date: str
    raw_results_path: Path
    raw_market_path: Path | None
    raw_detail_path: Path | None
    normalized_output_path: Path
    feature_output_path: Path | None
    result_matches: int
    market_rows: int
    normalized_rows: int
    feature_rows: int | None
    odds_available_count: int
    odds_notice: str


def collect_hkjc_history(
    *,
    start_date: str,
    end_date: str,
    raw_output_dir: Path,
    normalized_output_path: Path,
    feature_output_path: Path | None,
    timeout_seconds: int = 20,
    detail_fb_odds_types: list[str] | None = None,
) -> HKJCHistoryCollectionSummary:
    """Collect HKJC historical match data.

    Note: HKJC GraphQL API only provides HDC odds for active (live/upcoming)
    matches, not for settled matches. Historical odds will be all-None for
    matches with status INPLAYMATCHENDED. For backtesting, use NON_HKJC data
    (football-data.co.uk) which includes Asian Handicap closing odds.
    """
    provider = HKJCFootballProvider()
    adapter = DefaultHKJCAdapter()

    raw_output_dir.mkdir(parents=True, exist_ok=True)
    normalized_output_path.parent.mkdir(parents=True, exist_ok=True)
    if feature_output_path is not None:
        feature_output_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch results (match outcomes with scores)
    matches = provider.fetch_results_snapshot(
        start_date=start_date,
        end_date=end_date,
        timeout=timeout_seconds,
    )
    result_rows = _build_result_rows(matches)
    result_df = pd.DataFrame(result_rows)
    raw_results_path = raw_output_dir / "match_results.csv"
    result_df.to_csv(raw_results_path, index=False)

    # Step 2: For each match, fetch detail and extract HDC odds (if available)
    fb_odds_types = detail_fb_odds_types or ["HDC", "CHD", "HAD", "TTG", "ETG", "CHL", "ECH", "CHD", "ECD"]
    detail_market_rows: list[dict[str, Any]] = []
    detail_records: list[dict[str, Any]] = []
    odds_available_count = 0

    for row in result_rows:
        match_id = str(row.get("match_id") or "").strip()
        if not match_id:
            continue
        detail = provider.fetch_result_detail_snapshot(
            match_id=match_id,
            timeout=timeout_seconds,
            fb_odds_types=fb_odds_types,
        )
        detail_records.append({
            "match_id": match_id,
            "has_detail": bool(isinstance(detail, dict)),
            "detail_json": "" if detail is None else json.dumps(detail, ensure_ascii=False),
        })

        # Extract HDC odds from foPools
        hdc_rows = _extract_hdc_from_detail(
            match_id=match_id,
            result_row=row,
            detail=detail,
        )
        detail_market_rows.extend(hdc_rows)
        if any(r.get("odds_home") is not None or r.get("odds_away") is not None for r in hdc_rows):
            odds_available_count += 1

    market_df = pd.DataFrame(detail_market_rows)
    raw_market_path = raw_output_dir / "market_hdc_rows.csv"
    market_df.to_csv(raw_market_path, index=False)

    detail_df = pd.DataFrame(detail_records)
    raw_detail_path = raw_output_dir / "result_details.csv"
    detail_df.to_csv(raw_detail_path, index=False)

    # Step 3: Build normalized frame
    normalized_df = _build_normalized_history_frame(
        result_df=result_df,
        market_df=market_df,
        odds_available_count=odds_available_count,
    )
    normalized_df.to_csv(normalized_output_path, index=False)

    # Build notice about odds availability
    odds_notice = _build_odds_notice(
        start_date=start_date,
        end_date=end_date,
        result_count=len(result_df),
        odds_available_count=odds_available_count,
    )

    feature_rows_count: int | None = None
    if feature_output_path is not None:
        if not normalized_df.empty:
            build_feature_pipeline(input_path=normalized_output_path, output_path=feature_output_path)
            feature_rows_count = int(len(pd.read_csv(feature_output_path))) if feature_output_path.exists() else 0
        else:
            # Create empty output if no normalized data
            feature_output_path.write_text("")
            feature_rows_count = 0

    return HKJCHistoryCollectionSummary(
        start_date=start_date,
        end_date=end_date,
        raw_results_path=raw_results_path,
        raw_market_path=raw_market_path,
        raw_detail_path=raw_detail_path,
        normalized_output_path=normalized_output_path,
        feature_output_path=feature_output_path,
        result_matches=int(len(result_df)),
        market_rows=int(len(market_df)),
        normalized_rows=int(len(normalized_df)),
        feature_rows=feature_rows_count,
        odds_available_count=odds_available_count,
        odds_notice=odds_notice,
    )


def _extract_hdc_from_detail(
    match_id: str,
    result_row: dict[str, Any],
    detail: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract HDC (Asian Handicap) odds from a result detail snapshot's foPools."""
    if not isinstance(detail, dict):
        return []

    pools = detail.get("foPools")
    if not isinstance(pools, list):
        return []

    hdc_pool = next(
        (p for p in pools if isinstance(p, dict) and str(p.get("oddsType") or "").upper() == "HDC"),
        None,
    )
    if hdc_pool is None:
        return []

    lines = hdc_pool.get("lines")
    if not isinstance(lines, list):
        return []

    rows: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        condition = str(line.get("condition") or "").strip()
        handicap_line = _parse_hdc_condition(condition)
        line_is_main = bool(line.get("main"))
        combinations = line.get("combinations")
        if not isinstance(combinations, list):
            continue

        odds_home: float | None = None
        odds_away: float | None = None
        for combo in combinations:
            if not isinstance(combo, dict):
                continue
            side = str(combo.get("str") or "").upper()
            current = _safe_float(combo.get("currentOdds"))
            if side == "H":
                odds_home = current
            elif side == "A":
                odds_away = current

        lines_found = int(len(lines))
        line_id = str(line.get("lineId") or line.get("id") or f"l{len(rows)}").strip()
        provider_match_id = f"{match_id}_HDC_{line_id}"

        rows.append({
            "match_id": match_id,
            "provider_match_id": provider_match_id,
            "match_number": str(result_row.get("match_number", "")),
            "competition": result_row.get("competition", ""),
            "competition_ch": result_row.get("competition_ch", ""),
            "home_team_name": result_row.get("home_team_name", ""),
            "away_team_name": result_row.get("away_team_name", ""),
            "home_team_name_ch": result_row.get("home_team_name_ch", ""),
            "away_team_name_ch": result_row.get("away_team_name_ch", ""),
            "kickoff_time_utc": result_row.get("kickoff_time_utc", ""),
            "snapshot_time_utc": result_row.get("kickoff_time_utc", ""),
            "handicap_line": handicap_line if handicap_line is not None else 0.0,
            "odds_home": odds_home,
            "odds_away": odds_away,
            "line_is_main": line_is_main,
            "handicap_condition_raw": condition,
            "source_market": "HKJC",
            "lines_total": lines_found,
        })
    return rows


def _parse_hdc_condition(condition: str) -> float | None:
    """Parse Asian Handicap condition string like '+0.5', '-1.25', '0'."""
    if not condition.strip():
        return None
    try:
        return float(condition)
    except (ValueError, TypeError):
        return None


def _build_odds_notice(
    start_date: str,
    end_date: str,
    result_count: int,
    odds_available_count: int,
) -> str:
    """Build a human-readable notice about odds data availability."""
    if result_count == 0:
        return "No match results found in the specified date range."

    if odds_available_count > 0:
        return (
            f"HDC odds available for {odds_available_count}/{result_count} matches. "
            "Odds are only available while matches are live or upcoming — "
            "settled matches have all-None odds in the HKJC API."
        )

    return (
        "WARNING: All {result_count} matches are settled (INPLAYMATCHENDED). "
        "The HKJC GraphQL API does NOT provide historical HDC odds for settled matches. "
        "For backtesting, use NON_HKJC data from football-data.co.uk which includes "
        "Asian Handicap closing odds for major leagues. "
        "Alternatively, enable continuous live polling to accumulate odds snapshots "
        "before matches end."
    )


def _build_result_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches:
        match_id = str(match.get("id") or "").strip()
        if not match_id:
            continue

        score = extract_full_time_score(match.get("results"))
        kickoff_raw = match.get("kickOffTime")
        kickoff_text = str(kickoff_raw).strip() if kickoff_raw is not None else ""
        kickoff = pd.to_datetime(kickoff_text, utc=True, errors="coerce") if kickoff_text else pd.NaT
        rows.append(
            {
                "match_id": match_id,
                "provider_match_id": match_id,
                "match_number": str(match.get("frontEndId") or match.get("matchNumber") or "").strip(),
                "competition": _nested_name(match.get("tournament")),
                "competition_ch": _nested_name_ch(match.get("tournament")),
                "home_team_name": _nested_name(match.get("homeTeam")),
                "away_team_name": _nested_name(match.get("awayTeam")),
                "home_team_name_ch": _nested_name_ch(match.get("homeTeam")),
                "away_team_name_ch": _nested_name_ch(match.get("awayTeam")),
                "kickoff_time_utc": kickoff.isoformat() if not pd.isna(kickoff) else None,
                "ft_home_goals": score.get("full_time_home_score"),
                "ft_away_goals": score.get("full_time_away_score"),
                "result_confirm_type": score.get("result_confirm_type"),
                "payout_confirmed": score.get("payout_confirmed"),
                "source_market": "HKJC",
            }
        )
    return rows


def _build_normalized_history_frame(
    result_df: pd.DataFrame,
    market_df: pd.DataFrame,
    odds_available_count: int = 0,
) -> pd.DataFrame:
    columns = [
        "provider_match_id",
        "match_id",
        "competition",
        "competition_ch",
        "home_team_name",
        "away_team_name",
        "home_team_name_ch",
        "away_team_name_ch",
        "kickoff_time_utc",
        "handicap_open_line",
        "handicap_close_line",
        "odds_home_open",
        "odds_away_open",
        "odds_home_close",
        "odds_away_close",
        "source_market",
        "handicap_side",
        "target_handicap_side",
        "ft_home_goals",
        "ft_away_goals",
        "entry_snapshot_time_utc",
        "closing_snapshot_time_utc",
    ]

    if result_df.empty:
        return pd.DataFrame(columns=columns)

    # Use detail-extracted market rows if available
    if market_df.empty:
        # Still produce rows with result data but no odds
        rows: list[dict[str, Any]] = []
        for _, row in result_df.iterrows():
            rows.append({
                "provider_match_id": str(row.get("provider_match_id", "")),
                "match_id": str(row.get("match_id", "")),
                "competition": row.get("competition"),
                "competition_ch": row.get("competition_ch"),
                "home_team_name": row.get("home_team_name"),
                "away_team_name": row.get("away_team_name"),
                "home_team_name_ch": row.get("home_team_name_ch"),
                "away_team_name_ch": row.get("away_team_name_ch"),
                "kickoff_time_utc": row.get("kickoff_time_utc"),
                "handicap_open_line": None,
                "handicap_close_line": None,
                "odds_home_open": None,
                "odds_away_open": None,
                "odds_home_close": None,
                "odds_away_close": None,
                "source_market": "HKJC",
                "handicap_side": "home",
                "target_handicap_side": "home",
                "ft_home_goals": row.get("ft_home_goals"),
                "ft_away_goals": row.get("ft_away_goals"),
                "entry_snapshot_time_utc": None,
                "closing_snapshot_time_utc": None,
            })
        output_df = pd.DataFrame(rows)
        output_df = output_df.sort_values(["kickoff_time_utc", "provider_match_id"]).reset_index(drop=True)
        return output_df

    # Market data available — join with results
    market_by_match: dict[str, pd.DataFrame] = {}
    if not market_df.empty and "match_id" in market_df.columns:
        market_df = market_df.copy()
        market_df["match_id"] = market_df["match_id"].astype(str)
        market_df["snapshot_time_utc"] = pd.to_datetime(market_df["snapshot_time_utc"], utc=True, errors="coerce")
        market_df["handicap_line"] = pd.to_numeric(market_df["handicap_line"], errors="coerce")
        market_df["odds_home"] = pd.to_numeric(market_df["odds_home"], errors="coerce")
        market_df["odds_away"] = pd.to_numeric(market_df["odds_away"], errors="coerce")
        for match_id, group in market_df.groupby("match_id"):
            market_by_match[str(match_id)] = group.sort_values("snapshot_time_utc").reset_index(drop=True)

    output_rows: list[dict[str, Any]] = []
    for _, row in result_df.iterrows():
        match_id = str(row.get("match_id") or "").strip()
        market_group = market_by_match.get(match_id)
        line_prices = _select_open_close_prices(market_group)

        output_rows.append(
            {
                "provider_match_id": str(row.get("provider_match_id") or match_id),
                "match_id": match_id,
                "competition": row.get("competition"),
                "competition_ch": row.get("competition_ch"),
                "home_team_name": row.get("home_team_name"),
                "away_team_name": row.get("away_team_name"),
                "home_team_name_ch": row.get("home_team_name_ch"),
                "away_team_name_ch": row.get("away_team_name_ch"),
                "kickoff_time_utc": row.get("kickoff_time_utc"),
                "handicap_open_line": line_prices["handicap_open_line"],
                "handicap_close_line": line_prices["handicap_close_line"],
                "odds_home_open": line_prices["odds_home_open"],
                "odds_away_open": line_prices["odds_away_open"],
                "odds_home_close": line_prices["odds_home_close"],
                "odds_away_close": line_prices["odds_away_close"],
                "source_market": "HKJC",
                "handicap_side": "home",
                "target_handicap_side": "home",
                "ft_home_goals": row.get("ft_home_goals"),
                "ft_away_goals": row.get("ft_away_goals"),
                "entry_snapshot_time_utc": line_prices["entry_snapshot_time_utc"],
                "closing_snapshot_time_utc": line_prices["closing_snapshot_time_utc"],
            }
        )

    output_df = pd.DataFrame(output_rows)
    output_df["kickoff_time_utc"] = pd.to_datetime(output_df["kickoff_time_utc"], utc=True, errors="coerce")
    output_df = output_df.sort_values(["kickoff_time_utc", "provider_match_id"]).reset_index(drop=True)
    output_df["kickoff_time_utc"] = output_df["kickoff_time_utc"].apply(
        lambda ts: ts.isoformat() if not pd.isna(ts) else None
    )
    return output_df


def _select_open_close_prices(market_group: pd.DataFrame | None) -> dict[str, Any]:
    empty = {
        "handicap_open_line": None,
        "handicap_close_line": None,
        "odds_home_open": None,
        "odds_away_open": None,
        "odds_home_close": None,
        "odds_away_close": None,
        "entry_snapshot_time_utc": None,
        "closing_snapshot_time_utc": None,
    }
    if market_group is None or market_group.empty:
        return empty

    group = market_group.copy()
    group = group.dropna(subset=["snapshot_time_utc", "handicap_line", "odds_home", "odds_away"])
    if group.empty:
        return empty

    if "line_is_main" in group.columns and group["line_is_main"].astype(bool).any():
        preferred = group[group["line_is_main"].astype(bool)].copy()
    else:
        preferred = group.copy()

    preferred = preferred.sort_values("snapshot_time_utc").reset_index(drop=True)
    latest = preferred.iloc[-1]
    latest_line = float(latest["handicap_line"])

    same_line = preferred[preferred["handicap_line"].apply(lambda value: abs(float(value) - latest_line) < 1e-9)]
    selected = same_line if not same_line.empty else preferred

    entry = selected.iloc[0]
    closing = selected.iloc[-1]

    return {
        "handicap_open_line": _safe_float(entry.get("handicap_line")),
        "handicap_close_line": _safe_float(closing.get("handicap_line")),
        "odds_home_open": _safe_float(entry.get("odds_home")),
        "odds_away_open": _safe_float(entry.get("odds_away")),
        "odds_home_close": _safe_float(closing.get("odds_home")),
        "odds_away_close": _safe_float(closing.get("odds_away")),
        "entry_snapshot_time_utc": _to_iso_or_none(entry.get("snapshot_time_utc")),
        "closing_snapshot_time_utc": _to_iso_or_none(closing.get("snapshot_time_utc")),
    }


def _nested_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name_en") or value.get("name_ch") or "").strip()


def _nested_name_ch(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name_ch") or value.get("name_en") or "").strip()


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return None


def _to_iso_or_none(value: Any) -> str | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.isoformat()
