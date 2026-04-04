from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.live_feed.models import ExternalMarketEvent
from src.live_feed.providers.hkjc_request_debug import (
    RequestCandidate,
    build_frontend_match_list_variables,
    build_frontend_match_result_details_variables,
    replay_request_candidate,
    resolve_request_candidate,
    summarize_candidate,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class HKJCFootballProvider:
    provider_name: str = "hkjc"
    _session: requests.Session = field(init=False, repr=False)
    _last_raw_snapshot: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._session = _build_session()
        self._last_raw_snapshot = None

    def fetch_market_snapshot(
        self, *, as_of_utc: datetime, poll_timeout_seconds: int
    ) -> list[ExternalMarketEvent]:
        base_time = as_of_utc if as_of_utc.tzinfo is not None else as_of_utc.replace(tzinfo=timezone.utc)
        candidate, resolution_meta = resolve_request_candidate(
            mode="handicap",
            session=self._session,
            timeout=poll_timeout_seconds,
        )

        response_meta = replay_request_candidate(candidate, session=self._session, timeout=poll_timeout_seconds)
        response_json = response_meta.get("response_json")
        matches_data = _extract_matches(response_json)

        events: list[ExternalMarketEvent] = []
        for match_record in matches_data:
            for payload in _match_to_event_payloads(
                match_record=match_record,
                snapshot_time=base_time,
                request_mode=candidate.request_mode,
                transport_mode=candidate.transport_mode,
            ):
                events.append(ExternalMarketEvent(provider_name=self.provider_name, payload=payload))

        provider_debug = {
            "provider": self.provider_name,
            "mode": "handicap",
            "selected_candidate": summarize_candidate(candidate),
            "resolution_meta": resolution_meta,
            "status_code": response_meta.get("status_code"),
            "response_errors": response_meta.get("response_errors"),
            "match_count": len(matches_data),
            "event_count": len(events),
        }
        self._last_raw_snapshot = {
            "provider": self.provider_name,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "request_meta": _build_request_meta(candidate),
            "response_status_code": response_meta.get("status_code"),
            "response_content_type": response_meta.get("response_content_type"),
            "response_preview": response_meta.get("response_preview"),
            "response_text": response_meta.get("response_text"),
            "response_errors": response_meta.get("response_errors"),
            "raw_response": response_json,
            "provider_debug": provider_debug,
        }

        if not isinstance(response_json, dict):
            LOGGER.warning("HKJC request did not return JSON data.")
            return []
        if response_meta.get("response_errors"):
            LOGGER.warning("HKJC server returned GraphQL errors: %s", response_meta.get("response_errors"))
        if not matches_data:
            LOGGER.info("HKJC returned zero HDC rows for the current frontend request shape.")
            return []

        LOGGER.info(
            "HKJC frontend replay parsed %d event payloads from %d matches",
            len(events),
            len(matches_data),
        )
        return events

    def fetch_market_snapshot_for_range(
        self,
        *,
        start_date: str,
        end_date: str,
        poll_timeout_seconds: int = 20,
    ) -> list[ExternalMarketEvent]:
        """Fetch HDC market rows for a date range via the validated frontend GraphQL request shape."""
        candidate, resolution_meta = resolve_request_candidate(
            mode="handicap",
            session=self._session,
            timeout=poll_timeout_seconds,
        )
        candidate = replace(
            candidate,
            variables=build_frontend_match_list_variables(
                page="HDC",
                date_from=start_date,
                date_to=end_date,
                start_index=0,
                end_index=1000,
            ),
        )

        response_meta = replay_request_candidate(candidate, session=self._session, timeout=poll_timeout_seconds)
        response_json = response_meta.get("response_json")
        matches_data = _extract_matches(response_json)

        fetched_at = datetime.now(timezone.utc)
        events: list[ExternalMarketEvent] = []
        for match_record in matches_data:
            kickoff = _parse_kickoff(match_record.get("kickOffTime") or match_record.get("matchDate"), fetched_at)
            snapshot_time = kickoff if kickoff is not None else fetched_at
            for payload in _match_to_event_payloads(
                match_record=match_record,
                snapshot_time=snapshot_time,
                request_mode=candidate.request_mode,
                transport_mode=candidate.transport_mode,
            ):
                events.append(ExternalMarketEvent(provider_name=self.provider_name, payload=payload))

        self._last_raw_snapshot = {
            "provider": self.provider_name,
            "fetched_at_utc": fetched_at.isoformat(),
            "request_meta": _build_request_meta(candidate),
            "response_status_code": response_meta.get("status_code"),
            "response_content_type": response_meta.get("response_content_type"),
            "response_preview": response_meta.get("response_preview"),
            "response_text": response_meta.get("response_text"),
            "response_errors": response_meta.get("response_errors"),
            "raw_response": response_json,
            "provider_debug": {
                "provider": self.provider_name,
                "mode": "handicap-range",
                "date_range": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "selected_candidate": summarize_candidate(candidate),
                "resolution_meta": resolution_meta,
                "match_count": len(matches_data),
                "event_count": len(events),
            },
        }

        if response_meta.get("response_errors"):
            LOGGER.warning("HKJC handicap-range request returned GraphQL errors: %s", response_meta.get("response_errors"))
        return events

    def fetch_results_snapshot(
        self,
        *,
        start_date: str,
        end_date: str,
        timeout: int = 20,
    ) -> list[dict[str, Any]]:
        candidate, resolution_meta = resolve_request_candidate(
            mode="results",
            session=self._session,
            timeout=timeout,
            start_date=start_date,
            end_date=end_date,
        )
        candidate = replace(
            candidate,
            variables={
                **(candidate.variables or {}),
                "startDate": _normalize_results_date(start_date),
                "endDate": _normalize_results_date(end_date),
                "startIndex": 0,
                "endIndex": 1000,
            },
        )
        response_meta = replay_request_candidate(candidate, session=self._session, timeout=timeout)
        response_json = response_meta.get("response_json")
        matches_data = _extract_matches(response_json)
        self._last_raw_snapshot = {
            "provider": self.provider_name,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "request_meta": _build_request_meta(candidate),
            "response_status_code": response_meta.get("status_code"),
            "response_content_type": response_meta.get("response_content_type"),
            "response_preview": response_meta.get("response_preview"),
            "response_text": response_meta.get("response_text"),
            "response_errors": response_meta.get("response_errors"),
            "raw_response": response_json,
            "provider_debug": {
                "provider": self.provider_name,
                "mode": "results",
                "selected_candidate": summarize_candidate(candidate),
                "resolution_meta": resolution_meta,
                "match_count": len(matches_data),
            },
        }
        if response_meta.get("response_errors"):
            LOGGER.warning("HKJC results request returned GraphQL errors: %s", response_meta.get("response_errors"))
        return matches_data

    def fetch_result_detail_snapshot(
        self,
        *,
        match_id: str,
        timeout: int = 20,
        fb_odds_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        candidate, resolution_meta = resolve_request_candidate(
            mode="results-detail",
            session=self._session,
            timeout=timeout,
        )
        detail_variables = build_frontend_match_result_details_variables(
            match_id=match_id,
            fb_odds_types=fb_odds_types,
        )
        candidate = replace(candidate, variables=detail_variables)

        response_meta = replay_request_candidate(candidate, session=self._session, timeout=timeout)
        response_json = response_meta.get("response_json")
        matches_data = _extract_matches(response_json)
        detail = matches_data[0] if matches_data else None

        self._last_raw_snapshot = {
            "provider": self.provider_name,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "request_meta": _build_request_meta(candidate),
            "response_status_code": response_meta.get("status_code"),
            "response_content_type": response_meta.get("response_content_type"),
            "response_preview": response_meta.get("response_preview"),
            "response_text": response_meta.get("response_text"),
            "response_errors": response_meta.get("response_errors"),
            "raw_response": response_json,
            "provider_debug": {
                "provider": self.provider_name,
                "mode": "results-detail",
                "match_id": match_id,
                "selected_candidate": summarize_candidate(candidate),
                "resolution_meta": resolution_meta,
                "match_count": len(matches_data),
                "has_detail": detail is not None,
            },
        }
        if response_meta.get("response_errors"):
            LOGGER.warning("HKJC result detail request returned GraphQL errors: %s", response_meta.get("response_errors"))
        return detail

    def get_last_raw_snapshot(self) -> dict[str, Any] | None:
        return self._last_raw_snapshot


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        read=2,
        connect=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _extract_matches(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    matches = data.get("matches")
    return [match for match in matches if isinstance(match, dict)] if isinstance(matches, list) else []


def _match_to_event_payloads(
    *,
    match_record: dict[str, Any],
    snapshot_time: datetime,
    request_mode: str,
    transport_mode: str,
) -> list[dict[str, Any]]:
    match_id = str(match_record.get("id") or "").strip()
    if not match_id:
        return []

    home_obj = match_record.get("homeTeam") or {}
    away_obj = match_record.get("awayTeam") or {}
    tournament_obj = match_record.get("tournament") or {}
    front_end_id = str(match_record.get("frontEndId") or match_record.get("matchNumber") or "").strip()
    home_name = str(home_obj.get("name_en") or home_obj.get("name_ch") or "").strip()
    away_name = str(away_obj.get("name_en") or away_obj.get("name_ch") or "").strip()
    if not home_name or not away_name:
        return []

    competition = str(tournament_obj.get("name_en") or tournament_obj.get("name_ch") or "HKJC").strip() or "HKJC"
    competition_ch = str(tournament_obj.get("name_ch") or "").strip()
    kickoff = _parse_kickoff(match_record.get("kickOffTime") or match_record.get("matchDate"), snapshot_time)
    fo_pools = match_record.get("foPools")
    if not isinstance(fo_pools, list):
        return []

    payloads: list[dict[str, Any]] = []
    for pool in fo_pools:
        if not isinstance(pool, dict):
            continue
        if str(pool.get("oddsType") or "").upper() != "HDC":
            continue
        lines = pool.get("lines")
        if not isinstance(lines, list):
            continue
        for line in lines:
            payload = _line_to_payload(
                match_id=match_id,
                front_end_id=front_end_id,
                competition=competition,
                competition_ch=competition_ch,
                kickoff=kickoff,
                snapshot_time=snapshot_time,
                match_status=str(match_record.get("status") or ""),
                pool=pool,
                line=line,
                home_name=home_name,
                away_name=away_name,
                home_name_ch=str(home_obj.get("name_ch") or home_name),
                away_name_ch=str(away_obj.get("name_ch") or away_name),
                home_code=str(home_obj.get("id") or ""),
                away_code=str(away_obj.get("id") or ""),
                request_mode=request_mode,
                transport_mode=transport_mode,
            )
            if payload is not None:
                payloads.append(payload)
    return payloads


def _line_to_payload(
    *,
    match_id: str,
    front_end_id: str,
    competition: str,
    competition_ch: str,
    kickoff: datetime,
    snapshot_time: datetime,
    match_status: str,
    pool: dict[str, Any],
    line: dict[str, Any],
    home_name: str,
    away_name: str,
    home_name_ch: str,
    away_name_ch: str,
    home_code: str,
    away_code: str,
    request_mode: str,
    transport_mode: str,
) -> dict[str, Any] | None:
    combinations = line.get("combinations")
    if not isinstance(combinations, list):
        return None

    odds_home: float | None = None
    odds_away: float | None = None
    for combination in combinations:
        if not isinstance(combination, dict):
            continue
        side = str(combination.get("str") or "").upper()
        current_odds = _optional_float(combination.get("currentOdds"))
        if current_odds is None or current_odds <= 1.0:
            continue
        if side == "H":
            odds_home = current_odds
        elif side == "A":
            odds_away = current_odds

    if odds_home is None or odds_away is None:
        return None

    handicap_condition = str(line.get("condition") or "").strip()
    handicap_line = _parse_hdc_condition(handicap_condition)
    line_id = str(line.get("lineId") or line.get("id") or "").strip()
    provider_match_id = "_".join(
        part for part in [match_id, str(pool.get("oddsType") or "").upper(), line_id] if part
    )
    return {
        "provider_match_id": provider_match_id,
        "match_id": match_id,
        "match_number": front_end_id,
        "market_id": "ah_ft",
        "competition": competition,
        "competition_ch": competition_ch,
        "kickoff_time_utc": kickoff.isoformat(),
        "snapshot_time_utc": snapshot_time.isoformat(),
        "home_team_name": home_name,
        "away_team_name": away_name,
        "home_team_name_ch": home_name_ch,
        "away_team_name_ch": away_name_ch,
        "home_team_code": home_code,
        "away_team_code": away_code,
        "handicap_line": handicap_line if handicap_line is not None else 0.0,
        "handicap_condition_raw": handicap_condition,
        "odds_home": odds_home,
        "odds_away": odds_away,
        "side_semantic": "home",
        "match_status": match_status,
        "line_is_main": bool(line.get("main")),
        "source_label": "HKJC",
        "source_market": "hkjc_hdc",
        "request_mode": request_mode,
        "transport_mode": transport_mode,
        "parse_status": "ok",
        "settlement_scope": "full_time_only",
    }


def _build_request_meta(candidate: RequestCandidate) -> dict[str, Any]:
    return {
        "source": candidate.source,
        "page_url": candidate.page_url,
        "endpoint_url": candidate.endpoint_url,
        "method": candidate.method,
        "operation_name": candidate.operation_name,
        "request_mode": candidate.request_mode,
        "transport_mode": candidate.transport_mode,
        "headers": candidate.headers,
        "variables": candidate.variables,
        "extensions": candidate.extensions,
        "sha256_hash": candidate.sha256_hash,
        "query": candidate.query,
        "notes": candidate.notes,
    }


def _parse_kickoff(raw: Any, fallback: datetime) -> datetime:
    if raw is None:
        return fallback
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return fallback
    text = str(raw).strip()
    if not text:
        return fallback
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(text, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return fallback


def _parse_hdc_condition(condition: str) -> float | None:
    text = condition.strip().lstrip("+")
    if not text or text in {"0", "0.0"}:
        return 0.0
    if "/" in text:
        parts = text.split("/")
        try:
            values = [float(part.strip().lstrip("+")) for part in parts if part.strip()]
        except ValueError:
            return None
        return sum(values) / len(values) if values else None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_results_date(value: str) -> str:
    stripped = value.strip()
    if len(stripped) == 8 and stripped.isdigit():
        return f"{stripped[:4]}-{stripped[4:6]}-{stripped[6:8]}"
    return stripped
