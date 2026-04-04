from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.live_feed.providers.hkjc_provider import HKJCFootballProvider
from src.live_feed.providers.hkjc_request_debug import inspect_request_sources
from src.live_feed.providers.hkjc_result_validator import extract_full_time_score
from src.strategy.settlement import settle_handicap_bet


DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES: list[str] = [
    "HDC",
    "EDC",
    "HAD",
    "EHA",
    "TTG",
    "ETG",
    "CHL",
    "ECH",
    "CHD",
    "ECD",
]

_HKJC_TO_INTERNAL_RESULT: dict[str, str] = {
    "WIN": "win",
    "LOSE": "lose",
    "DRAW": "push",
    "HALFDRAWHALFWIN": "half-win",
    "HALFDRAWHALFLOSE": "half-lose",
}


@dataclass(frozen=True)
class ResultsValidationRow:
    match_id: str
    competition: str
    home_team: str
    away_team: str
    handicap_line: float | None
    HKJC_result: str
    internal_result: str
    is_match: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "competition": self.competition,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "handicap_line": self.handicap_line,
            "HKJC_result": self.HKJC_result,
            "internal_result": self.internal_result,
            "is_match": self.is_match,
            "notes": self.notes,
        }


def resolve_results_detail_fb_odds_types(from_curl: Path | None) -> list[str]:
    if from_curl is None or not from_curl.exists():
        return list(DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES)

    try:
        report = inspect_request_sources(mode="results-detail", from_curl=from_curl)
    except Exception:
        return list(DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES)

    selected = report.selected_candidate
    if selected is None or not isinstance(selected.variables, dict):
        return list(DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES)

    raw_types = selected.variables.get("fbOddsTypes")
    if not isinstance(raw_types, list):
        return list(DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES)

    parsed = [str(item).strip().upper() for item in raw_types if str(item).strip()]
    return parsed or list(DEFAULT_RESULT_DETAIL_FB_ODDS_TYPES)


def build_results_validation_rows(
    *,
    provider: HKJCFootballProvider,
    start_date: str,
    end_date: str,
    detail_fb_odds_types: list[str],
) -> list[ResultsValidationRow]:
    matches = provider.fetch_results_snapshot(start_date=start_date, end_date=end_date)
    rows: list[ResultsValidationRow] = []

    for match in matches:
        if not isinstance(match, dict):
            continue
        match_id = str(match.get("id") or "").strip()
        if not match_id:
            continue

        home_team = _nested_team_name(match.get("homeTeam"))
        away_team = _nested_team_name(match.get("awayTeam"))
        competition = _nested_team_name(match.get("tournament"))

        score = extract_full_time_score(match.get("results"))
        home_goals = _optional_int(score.get("full_time_home_score"))
        away_goals = _optional_int(score.get("full_time_away_score"))
        if home_goals is None or away_goals is None:
            rows.append(
                ResultsValidationRow(
                    match_id=match_id,
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    handicap_line=None,
                    HKJC_result="unknown",
                    internal_result="unknown",
                    is_match=False,
                    notes="Missing full-time score in matchResults payload.",
                )
            )
            continue

        detail = provider.fetch_result_detail_snapshot(
            match_id=match_id,
            fb_odds_types=detail_fb_odds_types,
        )
        if not isinstance(detail, dict):
            rows.append(
                ResultsValidationRow(
                    match_id=match_id,
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    handicap_line=None,
                    HKJC_result="unknown",
                    internal_result="unknown",
                    is_match=False,
                    notes="matchResultDetails returned no detail payload.",
                )
            )
            continue

        hdc_rows = _extract_hdc_line_rows(detail)
        if not hdc_rows:
            rows.append(
                ResultsValidationRow(
                    match_id=match_id,
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    handicap_line=None,
                    HKJC_result="unknown",
                    internal_result="unknown",
                    is_match=False,
                    notes="No HDC line outcomes returned in result detail payload.",
                )
            )
            continue

        anchor = _find_anchor_line(hdc_rows=hdc_rows, goal_diff=home_goals - away_goals)
        if anchor is None:
            rows.append(
                ResultsValidationRow(
                    match_id=match_id,
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    handicap_line=None,
                    HKJC_result="unknown",
                    internal_result="unknown",
                    is_match=False,
                    notes="Cannot infer handicap line ladder from HDC statuses.",
                )
            )
            continue

        anchor_index, anchor_line, anchor_source = anchor
        for index, hdc in enumerate(hdc_rows):
            inferred_line = round(anchor_line - 0.25 * (index - anchor_index), 2)
            settlement = settle_handicap_bet(
                home_goals=home_goals,
                away_goals=away_goals,
                handicap_side="home",
                handicap_line=inferred_line,
                odds=2.0,
                stake=1.0,
            )
            hkjc_result = hdc["home_result"]
            rows.append(
                ResultsValidationRow(
                    match_id=match_id,
                    competition=competition,
                    home_team=home_team,
                    away_team=away_team,
                    handicap_line=inferred_line,
                    HKJC_result=hkjc_result,
                    internal_result=settlement.outcome,
                    is_match=(hkjc_result == settlement.outcome),
                    notes=(
                        f"line_index={index}; away_result={hdc['away_result']}; "
                        f"anchor={anchor_source}@index={anchor_index}"
                    ),
                )
            )

    return rows


def build_results_validation_report(
    *,
    provider: HKJCFootballProvider,
    start_date: str,
    end_date: str,
    detail_fb_odds_types: list[str],
) -> pd.DataFrame:
    rows = build_results_validation_rows(
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        detail_fb_odds_types=detail_fb_odds_types,
    )
    if not rows:
        return pd.DataFrame(
            columns=[
                "match_id",
                "competition",
                "home_team",
                "away_team",
                "handicap_line",
                "HKJC_result",
                "internal_result",
                "is_match",
                "notes",
            ]
        )
    return pd.DataFrame([row.to_dict() for row in rows])


def _extract_hdc_line_rows(detail: dict[str, Any]) -> list[dict[str, str]]:
    pools = detail.get("foPools")
    if not isinstance(pools, list):
        return []

    hdc_pool = None
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if str(pool.get("oddsType") or "").upper() == "HDC":
            hdc_pool = pool
            break
    if not isinstance(hdc_pool, dict):
        return []

    lines = hdc_pool.get("lines")
    if not isinstance(lines, list):
        return []

    extracted: list[dict[str, str]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        combinations = line.get("combinations")
        if not isinstance(combinations, list):
            continue

        home_status = ""
        away_status = ""
        for combination in combinations:
            if not isinstance(combination, dict):
                continue
            side = str(combination.get("str") or "").upper().strip()
            status = _normalize_hkjc_status(str(combination.get("status") or ""))
            if side == "H":
                home_status = status
            elif side == "A":
                away_status = status

        if home_status:
            extracted.append({"home_result": home_status, "away_result": away_status})
    return extracted


def _find_anchor_line(*, hdc_rows: list[dict[str, str]], goal_diff: int) -> tuple[int, float, str] | None:
    target_draw = -float(goal_diff)

    for index, row in enumerate(hdc_rows):
        if row["home_result"] == "push":
            return (index, target_draw, "draw")

    for index, row in enumerate(hdc_rows):
        if row["home_result"] == "half-win":
            return (index, target_draw + 0.25, "half-win")

    for index, row in enumerate(hdc_rows):
        if row["home_result"] == "half-lose":
            return (index, target_draw - 0.25, "half-lose")

    return None


def _normalize_hkjc_status(status: str) -> str:
    return _HKJC_TO_INTERNAL_RESULT.get(status.strip().upper(), "unknown")


def _nested_team_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name_en") or value.get("name_ch") or "").strip()


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None
