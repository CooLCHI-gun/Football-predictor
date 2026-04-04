from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Set, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.live_feed.providers.hkjc_provider import HKJCFootballProvider
from src.live_feed.providers.hkjc_request_debug import (
    build_frontend_match_result_details_variables,
    build_frontend_match_results_variables,
    replay_request_candidate,
    resolve_request_candidate,
)

KEYWORDS = ("injury", "absence", "squad", "lineup")
PROXY_KEYWORDS = (
    "player",
    "selection",
    "starter",
    "status",
    "suspend",
    "inplay",
    "runningresult",
    "expectedsuspenddatetime",
)

TEAM_FIELD_CANDIDATES = [
    "injury_absence_index",
    "squad_absence_score",
    "injuryIndex",
    "absenceScore",
    "injuredCount",
    "missingPlayers",
    "lineupStatus",
    "suspensionCount",
]

MATCH_FIELD_CANDIDATES = [
    "injury_absence_index_home",
    "injury_absence_index_away",
    "squad_absence_score_home",
    "squad_absence_score_away",
    "homeInjuredCount",
    "awayInjuredCount",
]

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.2


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return "NET-TIMEOUT"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "NET-CONNECTION"
    if isinstance(exc, requests.exceptions.RequestException):
        return "NET-REQUEST"
    if isinstance(exc, json.JSONDecodeError):
        return "DATA-JSON"
    if isinstance(exc, KeyError):
        return "DATA-SCHEMA"
    return "FATAL"


def _run_with_retry(
    *,
    step_label: str,
    action: Callable[[], Any],
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # pragma: no cover - best effort diagnostic path
            last_error = exc
            level = _classify_error(exc)
            print(f"[ERROR][{level}] step={step_label} attempt={attempt}/{attempts} detail={exc}")
            retryable = level.startswith("NET-")
            if not retryable or attempt >= attempts:
                break
            sleep_seconds = backoff_seconds * attempt
            print(f"[RETRY] step={step_label} wait={sleep_seconds:.1f}s")
            time.sleep(sleep_seconds)
    raise RuntimeError(f"step_failed:{step_label}") from last_error


def walk_keys(obj: Any, path: str = "") -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else key
            rows.append((next_path, key))
            rows.extend(walk_keys(value, next_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            next_path = f"{path}[{index}]"
            rows.extend(walk_keys(value, next_path))
    return rows


def find_keyword_key_paths(obj: Any) -> List[str]:
    hits: List[str] = []
    seen: Set[str] = set()
    for full_path, key in walk_keys(obj):
        lowered = key.lower()
        if any(keyword in lowered for keyword in KEYWORDS):
            normalized = full_path
            if normalized not in seen:
                seen.add(normalized)
                hits.append(normalized)
    return hits


def check_file(file_path: Path) -> None:
    if not file_path.exists():
        print(f"[MISS] {file_path}")
        return
    data = json.loads(file_path.read_text(encoding="utf-8"))
    hits = find_keyword_key_paths(data)
    print(f"\n[FILE] {file_path}")
    print(f"[HITS] {len(hits)}")
    for item in hits[:120]:
        print(f" - {item}")


def check_file_with_keywords(file_path: Path, *, keywords: Tuple[str, ...], label: str) -> None:
    if not file_path.exists():
        print(f"[MISS] {file_path}")
        return
    data = json.loads(file_path.read_text(encoding="utf-8"))
    hits = find_keyword_key_paths_custom(data, keywords=keywords)
    print(f"\n[{label}] {file_path}")
    print(f"[{label}] hits={len(hits)}")
    for item in hits[:60]:
        print(f" - {item}")


def find_keyword_key_paths_custom(obj: Any, *, keywords: Tuple[str, ...]) -> List[str]:
    hits: List[str] = []
    seen: Set[str] = set()
    for full_path, key in walk_keys(obj):
        lowered = key.lower()
        if any(keyword in lowered for keyword in keywords):
            if full_path not in seen:
                seen.add(full_path)
                hits.append(full_path)
    return hits


def _build_probe_query(*, target: str, field_name: str) -> str:
    if target == "match":
        field_block = f"id {field_name}"
    else:
        field_block = f"id {target}Team {{ id {field_name} }}"
    return (
        "query probeField($startIndex: Int, $endIndex: Int,$startDate: String, $endDate: String, "
        "$matchIds: [String], $tournIds: [String], $fbOddsTypes: [FBOddsType]!, $fbOddsTypesM: [FBOddsType]!, "
        "$inplayOnly: Boolean, $featuredMatchesOnly: Boolean, $frontEndIds: [String], "
        "$earlySettlementOnly: Boolean, $showAllMatch: Boolean) { "
        "matches(startIndex: $startIndex,endIndex: $endIndex, startDate: $startDate, endDate: $endDate, "
        "matchIds: $matchIds, tournIds: $tournIds, fbOddsTypes: $fbOddsTypesM, inplayOnly: $inplayOnly, "
        "featuredMatchesOnly: $featuredMatchesOnly, frontEndIds: $frontEndIds, "
        "earlySettlementOnly: $earlySettlementOnly, showAllMatch: $showAllMatch) { "
        f"{field_block}" 
        " } }"
    )


def _collect_values(obj: Any, key: str) -> List[Any]:
    out: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out.extend(_collect_values(v, key))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_values(item, key))
    return out


def probe_graphql_fields() -> None:
    session = requests.Session()
    try:
        candidate, _ = _run_with_retry(
            step_label="resolve-candidate:handicap",
            action=lambda: resolve_request_candidate(mode="handicap", session=session, timeout=25),
        )
    except Exception as exc:
        print(f"[ERROR][GRAPHQL-BOOT] skip probe_graphql_fields detail={exc}")
        return

    endpoint = candidate.endpoint_url
    headers = dict(candidate.headers or {})
    variables = dict(candidate.variables or {})
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")

    print("\n[GRAPHQL] probing match-level candidates")
    _probe_target_fields(
        session=session,
        endpoint=endpoint,
        headers=headers,
        variables=variables,
        target="match",
        field_candidates=MATCH_FIELD_CANDIDATES,
    )

    print("\n[GRAPHQL] probing homeTeam candidates")
    _probe_target_fields(
        session=session,
        endpoint=endpoint,
        headers=headers,
        variables=variables,
        target="home",
        field_candidates=TEAM_FIELD_CANDIDATES,
    )

    print("\n[GRAPHQL] probing awayTeam candidates")
    _probe_target_fields(
        session=session,
        endpoint=endpoint,
        headers=headers,
        variables=variables,
        target="away",
        field_candidates=TEAM_FIELD_CANDIDATES,
    )


def _probe_target_fields(
    *,
    session: requests.Session,
    endpoint: str,
    headers: Dict[str, str],
    variables: Dict[str, Any],
    target: str,
    field_candidates: List[str],
) -> None:
    for field_name in field_candidates:
        payload = {
            "operationName": "probeField",
            "query": _build_probe_query(target=target, field_name=field_name),
            "variables": variables,
        }
        try:
            response = session.post(endpoint, headers=headers, json=payload, timeout=25)
            body = response.json()
        except Exception as exc:  # pragma: no cover
            print(f" - {field_name}: request_error={exc}")
            continue

        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            first_msg = str(errors[0].get("message", "")) if isinstance(errors[0], dict) else str(errors[0])
            if "Cannot query field" in first_msg:
                print(f" - {field_name}: not_in_schema")
            else:
                print(f" - {field_name}: graphql_error={first_msg}")
            continue

        values = _collect_values(body.get("data"), field_name)
        if not values:
            print(f" - {field_name}: in_schema_no_values")
            continue
        non_null = [value for value in values if value is not None]
        print(f" - {field_name}: in_schema values={len(values)} non_null={len(non_null)}")


def deep_probe_other_operations(output_root: Path) -> None:
    session = requests.Session()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    try:
        results_candidate, _ = _run_with_retry(
            step_label="resolve-candidate:results",
            action=lambda: resolve_request_candidate(
                mode="results",
                session=session,
                timeout=25,
                start_date=today,
                end_date=today,
            ),
        )
    except Exception as exc:
        print(f"[ERROR][RESULTS-BOOT] skip deep probe detail={exc}")
        return

    results_candidate = replace(
        results_candidate,
        variables=build_frontend_match_results_variables(start_date=today, end_date=today),
    )
    try:
        results_meta = _run_with_retry(
            step_label="replay-candidate:results",
            action=lambda: replay_request_candidate(results_candidate, session=session, timeout=25),
        )
    except Exception as exc:
        print(f"[ERROR][RESULTS-REPLAY] skip deep probe detail={exc}")
        return

    results_json = results_meta.get("response_json")
    results_path = output_root / "latest_raw_hkjc_probe_results.json"
    results_path.write_text(json.dumps(results_json, ensure_ascii=False, indent=2), encoding="utf-8")

    match_id = _first_match_id(results_json)
    print(f"\n[DEEP] results match_id={match_id or 'NONE'}")

    check_file_with_keywords(results_path, keywords=KEYWORDS, label="STRICT-RESULTS")
    check_file_with_keywords(results_path, keywords=PROXY_KEYWORDS, label="PROXY-RESULTS")

    if not match_id:
        print("[DEEP] no results match id; skip results-detail probing")
        return

    try:
        detail_candidate, _ = _run_with_retry(
            step_label="resolve-candidate:results-detail",
            action=lambda: resolve_request_candidate(mode="results-detail", session=session, timeout=25),
        )
    except Exception as exc:
        print(f"[ERROR][DETAIL-BOOT] skip results-detail probe detail={exc}")
        return

    detail_candidate = replace(
        detail_candidate,
        variables=build_frontend_match_result_details_variables(match_id=match_id),
    )
    try:
        detail_meta = _run_with_retry(
            step_label="replay-candidate:results-detail-default",
            action=lambda: replay_request_candidate(detail_candidate, session=session, timeout=25),
        )
    except Exception as exc:
        print(f"[ERROR][DETAIL-REPLAY] skip results-detail default detail={exc}")
        return

    detail_json = detail_meta.get("response_json")
    detail_path = output_root / "latest_raw_hkjc_probe_results_detail_default.json"
    detail_path.write_text(json.dumps(detail_json, ensure_ascii=False, indent=2), encoding="utf-8")

    check_file_with_keywords(detail_path, keywords=KEYWORDS, label="STRICT-DETAIL-DEFAULT")
    check_file_with_keywords(detail_path, keywords=PROXY_KEYWORDS, label="PROXY-DETAIL-DEFAULT")

    player_pool_sets: List[List[str]] = [
        ["FGS", "NGS"],
        ["NGS", "NTS"],
        ["FGS"],
    ]
    for index, fb_types in enumerate(player_pool_sets, start=1):
        try:
            detail_candidate_player, _ = _run_with_retry(
                step_label=f"resolve-candidate:results-detail-player-{index}",
                action=lambda: resolve_request_candidate(mode="results-detail", session=session, timeout=25),
            )
        except Exception as exc:
            print(f"[ERROR][DETAIL-PLAYER-BOOT] set={index} skip detail={exc}")
            continue

        detail_candidate_player = replace(
            detail_candidate_player,
            variables=build_frontend_match_result_details_variables(
                match_id=match_id,
                fb_odds_types=fb_types,
            ),
        )
        try:
            detail_meta_player = _run_with_retry(
                step_label=f"replay-candidate:results-detail-player-{index}",
                action=lambda: replay_request_candidate(detail_candidate_player, session=session, timeout=25),
            )
        except Exception as exc:
            print(f"[ERROR][DETAIL-PLAYER-REPLAY] set={index} skip detail={exc}")
            continue

        detail_json_player = detail_meta_player.get("response_json")
        output_path = output_root / f"latest_raw_hkjc_probe_results_detail_player_{index}.json"
        output_path.write_text(json.dumps(detail_json_player, ensure_ascii=False, indent=2), encoding="utf-8")

        errors = detail_meta_player.get("response_errors") or []
        print(f"\n[DEEP] player-set-{index} types={fb_types} errors={len(errors)}")
        if errors:
            print(f"[DEEP] player-set-{index} first_error={errors[0]}")

        check_file_with_keywords(output_path, keywords=KEYWORDS, label=f"STRICT-DETAIL-PLAYER-{index}")
        check_file_with_keywords(output_path, keywords=PROXY_KEYWORDS, label=f"PROXY-DETAIL-PLAYER-{index}")


def _first_match_id(results_json: Any) -> str | None:
    if not isinstance(results_json, dict):
        return None
    data = results_json.get("data")
    if not isinstance(data, dict):
        return None
    matches = data.get("matches")
    if not isinstance(matches, list) or not matches:
        return None
    first = matches[0]
    if not isinstance(first, dict):
        return None
    match_id = str(first.get("id") or "").strip()
    return match_id or None


def fetch_live_probe(output_path: Path) -> None:
    provider = HKJCFootballProvider()
    _run_with_retry(
        step_label="provider-fetch-market-snapshot",
        action=lambda: provider.fetch_market_snapshot(as_of_utc=datetime.now(timezone.utc), poll_timeout_seconds=25),
    )
    raw = provider.get_last_raw_snapshot() or {}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    matches = (((raw.get("raw_response") or {}).get("data") or {}).get("matches") or [])
    print(f"\n[FETCH] saved {output_path}")
    print(f"[FETCH] match_count={len(matches)}")


def main() -> None:
    check_file(Path("artifacts/live/raw/latest_raw_hkjc.json"))
    check_file(Path("artifacts/debug/latest_hkjc_request_debug_handicap.json"))

    probe_path = Path("artifacts/live/raw/latest_raw_hkjc_probe.json")
    try:
        fetch_live_probe(probe_path)
        check_file(probe_path)
    except Exception as exc:
        print(f"[ERROR][LIVE-PROBE] skip live probe detail={exc}")

    probe_graphql_fields()
    deep_probe_other_operations(Path("artifacts/live/raw"))


if __name__ == "__main__":
    main()
