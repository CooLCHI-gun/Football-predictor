from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HKJCValidatedResult:
    provider_match_id: str
    front_end_id: str | None
    competition: str | None
    home_team_name: str | None
    away_team_name: str | None
    kickoff_time_utc: str | None
    full_time_home_score: int | None
    full_time_away_score: int | None
    payout_confirmed: bool | None
    result_confirm_type: int | None
    result_sequence: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_results_snapshot(matches: list[dict[str, Any]]) -> list[HKJCValidatedResult]:
    validated: list[HKJCValidatedResult] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        score = extract_full_time_score(match.get("results"))
        validated.append(
            HKJCValidatedResult(
                provider_match_id=str(match.get("id") or ""),
                front_end_id=_optional_str(match.get("frontEndId")),
                competition=_nested_name(match.get("tournament")),
                home_team_name=_nested_name(match.get("homeTeam")),
                away_team_name=_nested_name(match.get("awayTeam")),
                kickoff_time_utc=_optional_str(match.get("kickOffTime")),
                full_time_home_score=score.get("full_time_home_score"),
                full_time_away_score=score.get("full_time_away_score"),
                payout_confirmed=score.get("payout_confirmed"),
                result_confirm_type=score.get("result_confirm_type"),
                result_sequence=score.get("result_sequence"),
            )
        )
    return validated


def extract_full_time_score(results: Any) -> dict[str, Any]:
    if not isinstance(results, list):
        return {
            "full_time_home_score": None,
            "full_time_away_score": None,
            "payout_confirmed": None,
            "result_confirm_type": None,
            "result_sequence": None,
        }

    typed_results = [item for item in results if isinstance(item, dict)]
    full_time_candidates = [
        item
        for item in typed_results
        if item.get("resultType") == 1
        and isinstance(item.get("homeResult"), int)
        and isinstance(item.get("awayResult"), int)
    ]
    stage_scoped_candidates = [
        item
        for item in full_time_candidates
        if item.get("stageId") in {2, 3, 4, 5}
    ]
    preferred = stage_scoped_candidates or full_time_candidates
    if not preferred:
        return {
            "full_time_home_score": None,
            "full_time_away_score": None,
            "payout_confirmed": None,
            "result_confirm_type": None,
            "result_sequence": None,
        }

    selected = max(preferred, key=_result_sort_key)
    return {
        "full_time_home_score": int(selected["homeResult"]),
        "full_time_away_score": int(selected["awayResult"]),
        "payout_confirmed": _optional_bool(selected.get("payoutConfirmed")),
        "result_confirm_type": _optional_int(selected.get("resultConfirmType")),
        "result_sequence": _optional_int(selected.get("sequence")),
    }


def _result_sort_key(result: dict[str, Any]) -> tuple[int, int]:
    return (_optional_int(result.get("sequence")) or -1, _optional_int(result.get("stageId")) or -1)


def _nested_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _optional_str(value.get("name_en") or value.get("name_ch"))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
