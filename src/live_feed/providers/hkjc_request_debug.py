from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import shlex
from typing import Any

import requests


HKJC_HANDICAP_PAGE_URL = "https://bet.hkjc.com/ch/football/hdc"
HKJC_RESULTS_PAGE_URL = "https://bet.hkjc.com/ch/football/results#detail"
HKJC_GRAPHQL_URL = "https://info.cld.hkjc.com/graphql/base/"
DEFAULT_DEBUG_DIR = Path("artifacts/debug")
DEFAULT_REPORT_NAME_TEMPLATE = "latest_hkjc_request_debug_{mode}.json"

_BUNDLE_MAIN_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']*/static/js/main\.[^\"']+\.js)[\"']", re.IGNORECASE)
_BUNDLE_VENDOR_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']*/static/js/vendors\.[^\"']+\.js)[\"']", re.IGNORECASE)
_OPERATION_MARKERS: dict[str, str] = {
    "matchList": "query matchList($startIndex: Int",
    "matchResults": "query matchResults($startDate: String",
    "matchResultDetails": "query matchResultDetails($matchId: String",
    "lastOdds": "query lastOdds($matchId: String",
}

# Exact frontend GraphQL documents extracted from the April 2026 JCBW main bundle.
# These are kept as stable fallbacks when bundle discovery is unavailable.
FRONTEND_MATCH_LIST_QUERY = """
query matchList($startIndex: Int, $endIndex: Int,$startDate: String, $endDate: String, $matchIds: [String], $tournIds: [String], $fbOddsTypes: [FBOddsType]!, $fbOddsTypesM: [FBOddsType]!, $inplayOnly: Boolean, $featuredMatchesOnly: Boolean, $frontEndIds: [String], $earlySettlementOnly: Boolean, $showAllMatch: Boolean) {
  matches(startIndex: $startIndex,endIndex: $endIndex, startDate: $startDate, endDate: $endDate, matchIds: $matchIds, tournIds: $tournIds, fbOddsTypes: $fbOddsTypesM, inplayOnly: $inplayOnly, featuredMatchesOnly: $featuredMatchesOnly, frontEndIds: $frontEndIds, earlySettlementOnly: $earlySettlementOnly, showAllMatch: $showAllMatch) {
    id
    frontEndId
    matchDate
    kickOffTime
    status
    updateAt
    sequence
    esIndicatorEnabled
    homeTeam {
      id
      name_en
      name_ch
    }
    awayTeam {
      id
      name_en
      name_ch
    }
    tournament {
      id
      frontEndId
      nameProfileId
      isInteractiveServiceAvailable
      code
      name_en
      name_ch
    }
    isInteractiveServiceAvailable
    inplayDelay
    venue {
      code
      name_en
      name_ch
    }
    tvChannels {
      code
      name_en
      name_ch
    }
    featureStartTime
    featureMatchSequence
    poolInfo {
      normalPools
      inplayPools
      sellingPools
      ntsInfo
      entInfo
      definedPools
      ngsInfo {
        str
        name_en
        name_ch
        instNo
      }
      agsInfo {
        str
        name_en
        name_ch
      }
    }
    runningResult {
      homeScore
      awayScore
      corner
      homeCorner
      awayCorner
    }
    runningResultExtra {
      homeScore
      awayScore
      corner
      homeCorner
      awayCorner
    }
    adminOperation {
      remark {
        typ
      }
    }
    foPools(fbOddsTypes: $fbOddsTypes) {
      id
      status
      oddsType
      instNo
      inplay
      name_ch
      name_en
      updateAt
      expectedSuspendDateTime
      lines {
        lineId
        status
        condition
        main
        combinations {
          combId
          str
          status
          offerEarlySettlement
          currentOdds
          selections {
            selId
            str
            name_ch
            name_en
          }
        }
      }
    }
  }
}
""".strip()

FRONTEND_MATCH_RESULTS_QUERY = """
query matchResults($startDate: String, $endDate: String, $startIndex: Int,$endIndex: Int,$teamId: String) {
  timeOffset {
    fb
  }
  matchNumByDate(startDate: $startDate, endDate: $endDate, teamId: $teamId) {
    total
  }
  matches: matchResult(startDate: $startDate, endDate: $endDate, startIndex: $startIndex,endIndex: $endIndex, teamId: $teamId) {
    id
    status
    frontEndId
    matchDayOfWeek
    matchNumber
    matchDate
    kickOffTime
    sequence
    homeTeam {
      id
      name_en
      name_ch
    }
    awayTeam {
      id
      name_en
      name_ch
    }
    tournament {
      code
      name_en
      name_ch
    }
    results {
      homeResult
      awayResult
      ttlCornerResult
      resultConfirmType
      payoutConfirmed
      stageId
      resultType
      sequence
    }
    poolInfo {
      payoutRefundPools
      refundPools
      ntsInfo
      entInfo
      definedPools
      ngsInfo {
        str
        name_en
        name_ch
        instNo
      }
      agsInfo {
        str
        name_en
        name_ch
      }
    }
  }
}
""".strip()

FRONTEND_MATCH_RESULT_DETAILS_QUERY = """
query matchResultDetails($matchId: String, $fbOddsTypes: [FBOddsType]!) {
    matches: matchResult(matchId: $matchId) {
        id
        foPools(fbOddsTypes: $fbOddsTypes, resultOnly: true) {
            id
            status
            oddsType
            instNo
            name_ch
            name_en
            lines(resultOnly: true) {
                combinations {
                    str
                    status
                    winOrd
                    selections {
                        selId
                        str
                        name_ch
                        name_en
                    }
                }
            }
        }
        additionalResults {
            resSetId
            results {
                awayResult
                homeResult
                ttlCornerResult
                mask
                payoutConfirmed
                resultConfirmType
                resultType
                sequence
                stageId
            }
        }
    }
}
""".strip()

_BASE_REQUEST_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": HKJC_HANDICAP_PAGE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class RequestCandidate:
    mode: str
    source: str
    endpoint_url: str
    method: str
    operation_name: str | None
    request_mode: str
    transport_mode: str
    headers: dict[str, str] = field(default_factory=dict)
    query: str | None = None
    variables: dict[str, Any] | None = None
    extensions: dict[str, Any] | None = None
    sha256_hash: str | None = None
    referer: str | None = None
    origin: str | None = None
    page_url: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RequestCandidate":
        return cls(
            mode=str(payload.get("mode") or "handicap"),
            source=str(payload.get("source") or "unknown"),
            endpoint_url=str(payload.get("endpoint_url") or HKJC_GRAPHQL_URL),
            method=str(payload.get("method") or "POST").upper(),
            operation_name=_read_optional_str(payload.get("operation_name")),
            request_mode=str(payload.get("request_mode") or "fallback-json"),
            transport_mode=str(payload.get("transport_mode") or "frontend-graphql-document"),
            headers=_string_dict(payload.get("headers")),
            query=_read_optional_str(payload.get("query")),
            variables=_object_dict(payload.get("variables")),
            extensions=_object_dict(payload.get("extensions")),
            sha256_hash=_read_optional_str(payload.get("sha256_hash")),
            referer=_read_optional_str(payload.get("referer")),
            origin=_read_optional_str(payload.get("origin")),
            page_url=_read_optional_str(payload.get("page_url")),
            notes=[str(item) for item in payload.get("notes", [])] if isinstance(payload.get("notes"), list) else [],
        )


@dataclass(frozen=True)
class InspectionReport:
    mode: str
    sources: list[str]
    selected_candidate: RequestCandidate | None
    candidates: list[RequestCandidate]
    summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sources": self.sources,
            "selected_candidate": self.selected_candidate.to_dict() if self.selected_candidate is not None else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "summary": self.summary,
            "notes": self.notes,
        }


def build_frontend_match_list_variables(
    *,
    page: str = "HDC",
    date_from: str | None = None,
    date_to: str | None = None,
    tourn_ids: list[str] | None = None,
    match_ids: list[str] | None = None,
    front_end_ids: list[str] | None = None,
    inplay_only: bool | None = None,
    featured_matches_only: bool = False,
    early_settlement_only: bool = False,
    show_all_match: bool = False,
    start_index: int | None = None,
    end_index: int | None = None,
) -> dict[str, Any]:
    page_upper = page.strip().upper() or "HDC"
    odds_types = _frontend_odds_types(page_upper)
    return {
        "fbOddsTypes": odds_types,
        "fbOddsTypesM": ["ALL"] if page_upper in {"OFM", "INPLAY"} else odds_types,
        "inplayOnly": ("INPLAY" in page_upper) if inplay_only is None else inplay_only,
        "featuredMatchesOnly": featured_matches_only or page_upper == "OFM",
        "startDate": _normalize_frontend_match_list_date(date_from),
        "endDate": _normalize_frontend_match_list_date(date_to),
        "tournIds": tourn_ids or None,
        "matchIds": match_ids or None,
        "tournId": None,
        "tournProfileId": None,
        "subType": _frontend_sub_type(page_upper),
        "startIndex": start_index,
        "endIndex": end_index,
        "frontEndIds": front_end_ids or None,
        "earlySettlementOnly": early_settlement_only,
        "showAllMatch": show_all_match,
        "tday": None,
        "tIdList": None,
    }


def build_frontend_match_results_variables(
    *,
    start_date: str,
    end_date: str,
    start_index: int = 0,
    end_index: int = 1000,
    team_id: str | None = None,
) -> dict[str, Any]:
    return {
        "startDate": _normalize_frontend_results_date(start_date),
        "endDate": _normalize_frontend_results_date(end_date),
        "startIndex": start_index,
        "endIndex": end_index,
        "teamId": team_id,
    }


def build_frontend_match_result_details_variables(
    *,
    match_id: str,
    fb_odds_types: list[str] | None = None,
) -> dict[str, Any]:
    normalized_match_id = match_id.strip()
    if not normalized_match_id:
        raise ValueError("match_id must not be empty for results detail requests.")
    return {
        "matchId": normalized_match_id,
        "fbOddsTypes": fb_odds_types or ["HDC", "EDC", "HAD", "EHA", "TTG", "ETG", "CHL", "ECH", "CHD", "ECD"],
    }


def build_default_candidate(mode: str, *, start_date: str | None = None, end_date: str | None = None) -> RequestCandidate:
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "results-detail":
        return RequestCandidate(
            mode="results-detail",
            source="embedded-default",
            endpoint_url=HKJC_GRAPHQL_URL,
            method="POST",
            operation_name="matchResultDetails",
            request_mode="fallback-json",
            transport_mode="frontend-graphql-document",
            headers=_base_headers(mode="results-detail"),
            query=FRONTEND_MATCH_RESULT_DETAILS_QUERY,
            variables=build_frontend_match_result_details_variables(match_id="""FB0001"""),
            referer=HKJC_RESULTS_PAGE_URL,
            origin="https://bet.hkjc.com",
            page_url=HKJC_RESULTS_PAGE_URL,
            notes=["Built from April 2026 JCBW bundle extraction."],
        )
    if normalized_mode == "results":
        return RequestCandidate(
            mode="results",
            source="embedded-default",
            endpoint_url=HKJC_GRAPHQL_URL,
            method="POST",
            operation_name="matchResults",
            request_mode="fallback-json",
            transport_mode="frontend-graphql-document",
            headers=_base_headers(mode="results"),
            query=FRONTEND_MATCH_RESULTS_QUERY,
            variables=build_frontend_match_results_variables(
                start_date=start_date or _today_iso_date(),
                end_date=end_date or _today_iso_date(),
            ),
            referer=HKJC_RESULTS_PAGE_URL,
            origin="https://bet.hkjc.com",
            page_url=HKJC_RESULTS_PAGE_URL,
            notes=["Built from April 2026 JCBW bundle extraction."],
        )
    return RequestCandidate(
        mode="handicap",
        source="embedded-default",
        endpoint_url=HKJC_GRAPHQL_URL,
        method="POST",
        operation_name="matchList",
        request_mode="fallback-json",
        transport_mode="frontend-graphql-document",
        headers=_base_headers(mode="handicap"),
        query=FRONTEND_MATCH_LIST_QUERY,
        variables=build_frontend_match_list_variables(page="HDC"),
        referer=HKJC_HANDICAP_PAGE_URL,
        origin="https://bet.hkjc.com",
        page_url=HKJC_HANDICAP_PAGE_URL,
        notes=["Built from April 2026 JCBW bundle extraction."],
    )


def inspect_request_sources(
    *,
    mode: str,
    from_har: Path | None = None,
    from_curl: Path | None = None,
    from_bundle: Path | None = None,
    from_html: Path | None = None,
) -> InspectionReport:
    normalized_mode = _normalize_mode(mode)
    sources: list[str] = []
    candidates: list[RequestCandidate] = []
    notes: list[str] = []

    if from_har is not None:
        sources.append(str(from_har))
        candidates.extend(_inspect_har_file(from_har, mode=normalized_mode))
    if from_curl is not None:
        sources.append(str(from_curl))
        curl_candidate = _inspect_curl_file(from_curl, mode=normalized_mode)
        if curl_candidate is not None:
            candidates.append(curl_candidate)
    if from_bundle is not None:
        sources.append(str(from_bundle))
        candidates.extend(_inspect_bundle_file(from_bundle, mode=normalized_mode))
    if from_html is not None:
        sources.append(str(from_html))
        notes.extend(_inspect_html_shell(from_html))

    if not candidates:
        candidates.append(build_default_candidate(normalized_mode))
        notes.append("No explicit request artifact matched. Falling back to the embedded frontend query shape.")

    selected_candidate = _select_best_candidate(candidates, mode=normalized_mode)
    summary = {
        "candidate_count": len(candidates),
        "persisted_candidate_count": sum(1 for candidate in candidates if candidate.sha256_hash),
        "graphql_endpoint_count": len({candidate.endpoint_url for candidate in candidates}),
    }
    return InspectionReport(
        mode=normalized_mode,
        sources=sources,
        selected_candidate=selected_candidate,
        candidates=candidates,
        summary=summary,
        notes=notes,
    )


def write_inspection_report(report: InspectionReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_candidate_from_debug_report(path: Path, *, mode: str) -> RequestCandidate | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    selected = payload.get("selected_candidate")
    if not isinstance(selected, dict):
        return None
    candidate = RequestCandidate.from_dict(selected)
    return candidate if candidate.mode == _normalize_mode(mode) else None


def discover_live_bundle_candidates(
    *,
    mode: str,
    session: requests.Session | None = None,
    timeout: int = 20,
) -> tuple[list[RequestCandidate], dict[str, Any]]:
    browser = session or requests.Session()
    page_url = HKJC_HANDICAP_PAGE_URL if _normalize_mode(mode) == "handicap" else HKJC_RESULTS_PAGE_URL
    html_response = browser.get(page_url, headers=_base_headers(mode=_normalize_mode(mode)), timeout=timeout)
    html_text = html_response.text
    main_bundle_url = _extract_bundle_url(html_text, _BUNDLE_MAIN_SRC_RE)
    vendor_bundle_url = _extract_bundle_url(html_text, _BUNDLE_VENDOR_SRC_RE)
    if main_bundle_url is None:
        raise ValueError("Could not locate HKJC main bundle URL from HTML shell.")

    main_response = browser.get(main_bundle_url, headers=_base_headers(mode=_normalize_mode(mode)), timeout=timeout)
    bundle_text = main_response.text
    candidates = _inspect_bundle_text(
        bundle_text,
        mode=_normalize_mode(mode),
        source=f"live-bundle:{main_bundle_url}",
        page_url=page_url,
    )
    metadata = {
        "page_url": page_url,
        "html_status": html_response.status_code,
        "html_length": len(html_text),
        "main_bundle_url": main_bundle_url,
        "main_bundle_status": main_response.status_code,
        "main_bundle_length": len(bundle_text),
        "vendor_bundle_url": vendor_bundle_url,
    }
    return candidates, metadata


def resolve_request_candidate(
    *,
    mode: str,
    session: requests.Session | None = None,
    timeout: int = 20,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[RequestCandidate, dict[str, Any]]:
    normalized_mode = _normalize_mode(mode)
    debug_report_path = debug_dir / DEFAULT_REPORT_NAME_TEMPLATE.format(mode=normalized_mode)
    artifact_candidate = load_candidate_from_debug_report(debug_report_path, mode=normalized_mode)
    if artifact_candidate is not None:
        return artifact_candidate, {
            "resolution_source": "debug-report",
            "debug_report_path": str(debug_report_path),
        }

    try:
        candidates, bundle_meta = discover_live_bundle_candidates(mode=normalized_mode, session=session, timeout=timeout)
        selected = _select_best_candidate(candidates, mode=normalized_mode)
        if selected is not None:
            return selected, {
                "resolution_source": "live-bundle",
                "bundle_meta": bundle_meta,
                "candidate_count": len(candidates),
            }
    except Exception as exc:  # pragma: no cover - defensive for volatile upstream pages
        return build_default_candidate(normalized_mode, start_date=start_date, end_date=end_date), {
            "resolution_source": "embedded-default",
            "resolution_error": str(exc),
        }

    return build_default_candidate(normalized_mode, start_date=start_date, end_date=end_date), {
        "resolution_source": "embedded-default",
    }


def replay_request_candidate(
    candidate: RequestCandidate,
    *,
    session: requests.Session | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    client = session or requests.Session()
    request_headers = dict(candidate.headers)
    if candidate.referer and "Referer" not in request_headers:
        request_headers["Referer"] = candidate.referer
    if candidate.origin and "Origin" not in request_headers:
        request_headers["Origin"] = candidate.origin

    response = client.request(
        method=candidate.method,
        url=candidate.endpoint_url,
        headers=request_headers,
        json=_candidate_json_body(candidate),
        timeout=timeout,
    )
    response_text = response.text
    response_json: dict[str, Any] | None = None
    response_errors: list[dict[str, Any]] | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            response_json = parsed
            raw_errors = parsed.get("errors")
            response_errors = [item for item in raw_errors if isinstance(item, dict)] if isinstance(raw_errors, list) else None
    except ValueError:
        response_json = None
    return {
        "status_code": response.status_code,
        "response_content_type": response.headers.get("Content-Type"),
        "response_text": response_text,
        "response_preview": response_text[:4000],
        "response_json": response_json,
        "response_errors": response_errors,
        "row_count": _infer_row_count(candidate.mode, response_json),
    }


def report_path_for_mode(mode: str, *, debug_dir: Path = DEFAULT_DEBUG_DIR) -> Path:
    return debug_dir / DEFAULT_REPORT_NAME_TEMPLATE.format(mode=_normalize_mode(mode))


def summarize_candidate(candidate: RequestCandidate | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    return {
        "mode": candidate.mode,
        "source": candidate.source,
        "endpoint_url": candidate.endpoint_url,
        "method": candidate.method,
        "operation_name": candidate.operation_name,
        "request_mode": candidate.request_mode,
        "transport_mode": candidate.transport_mode,
        "sha256_hash": candidate.sha256_hash,
        "referer": candidate.referer,
        "origin": candidate.origin,
        "variable_keys": sorted((candidate.variables or {}).keys()),
    }


def _inspect_har_file(path: Path, *, mode: str) -> list[RequestCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    log_payload = payload.get("log") if isinstance(payload, dict) else None
    entries = log_payload.get("entries") if isinstance(log_payload, dict) else None
    if not isinstance(entries, list):
        return []

    candidates: list[RequestCandidate] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request_payload = entry.get("request")
        if not isinstance(request_payload, dict):
            continue
        url = str(request_payload.get("url") or "")
        if not url:
            continue
        if "graphql" not in url.lower() and "football" not in url.lower():
            continue
        method = str(request_payload.get("method") or "GET").upper()
        headers = _har_headers_to_dict(request_payload.get("headers"))
        post_data = request_payload.get("postData")
        json_body = _parse_request_body(post_data.get("text") if isinstance(post_data, dict) else None)
        variables = _object_dict(json_body.get("variables")) if isinstance(json_body, dict) else None
        variables = _normalize_graphql_variables(variables)
        extensions = _object_dict(json_body.get("extensions")) if isinstance(json_body, dict) else None
        operation_name = _read_optional_str(json_body.get("operationName")) if isinstance(json_body, dict) else None
        query = _read_optional_str(json_body.get("query")) if isinstance(json_body, dict) else None
        sha256_hash = None
        if isinstance(extensions, dict):
            persisted_query = extensions.get("persistedQuery")
            if isinstance(persisted_query, dict):
                sha256_hash = _read_optional_str(persisted_query.get("sha256Hash"))
        referer = headers.get("Referer") or headers.get("referer")
        origin = headers.get("Origin") or headers.get("origin")
        candidate_mode = _mode_from_candidate_hint(mode=mode, operation_name=operation_name, query=query, referer=referer)
        if candidate_mode != mode:
            continue
        candidates.append(
            RequestCandidate(
                mode=candidate_mode,
                source=f"har:{path.name}",
                endpoint_url=url,
                method=method,
                operation_name=operation_name,
                request_mode="persisted-graphql" if sha256_hash else "fallback-json",
                transport_mode="graphql-request",
                headers=_safe_headers_subset(headers),
                query=query,
                variables=variables,
                extensions=extensions,
                sha256_hash=sha256_hash,
                referer=referer,
                origin=origin,
                page_url=referer,
                notes=["Parsed from HAR request entry."],
            )
        )
    return candidates


def _inspect_curl_file(path: Path, *, mode: str) -> RequestCandidate | None:
    text = path.read_text(encoding="utf-8")
    normalized_text = _decode_cmd_caret_escapes(text)
    tokens = shlex.split(normalized_text, posix=True)
    if not tokens:
        return None
    if tokens[0].lower() == "curl":
        tokens = tokens[1:]

    method = "GET"
    url = ""
    headers: dict[str, str] = {}
    data_text: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-X", "--request"} and index + 1 < len(tokens):
            method = tokens[index + 1].upper()
            index += 2
            continue
        if token in {"-H", "--header"} and index + 1 < len(tokens):
            key, value = _split_header(tokens[index + 1])
            if key:
                headers[key] = value
            index += 2
            continue
        if token in {"--data", "--data-raw", "--data-binary", "-d"} and index + 1 < len(tokens):
            data_text = tokens[index + 1]
            if method == "GET":
                method = "POST"
            index += 2
            continue
        if token == "--url" and index + 1 < len(tokens):
            url = tokens[index + 1]
            index += 2
            continue
        if token.startswith("http"):
            url = token
        index += 1

    if not url:
        return None

    payload = _parse_request_body(data_text)
    variables = _object_dict(payload.get("variables")) if isinstance(payload, dict) else None
    variables = _normalize_graphql_variables(variables)
    extensions = _object_dict(payload.get("extensions")) if isinstance(payload, dict) else None
    query = _read_optional_str(payload.get("query")) if isinstance(payload, dict) else None
    operation_name = _read_optional_str(payload.get("operationName")) if isinstance(payload, dict) else None
    sha256_hash = None
    if isinstance(extensions, dict):
        persisted_query = extensions.get("persistedQuery")
        if isinstance(persisted_query, dict):
            sha256_hash = _read_optional_str(persisted_query.get("sha256Hash"))
    candidate_mode = _mode_from_candidate_hint(
        mode=mode,
        operation_name=operation_name,
        query=query,
        referer=headers.get("Referer") or headers.get("referer"),
    )
    if candidate_mode != mode:
        return None
    return RequestCandidate(
        mode=candidate_mode,
        source=f"curl:{path.name}",
        endpoint_url=url,
        method=method,
        operation_name=operation_name,
        request_mode="persisted-graphql" if sha256_hash else "fallback-json",
        transport_mode="graphql-request",
        headers=_safe_headers_subset(headers),
        query=query,
        variables=variables,
        extensions=extensions,
        sha256_hash=sha256_hash,
        referer=headers.get("Referer") or headers.get("referer"),
        origin=headers.get("Origin") or headers.get("origin"),
        page_url=headers.get("Referer") or headers.get("referer"),
        notes=["Parsed from copied DevTools cURL request."],
    )


def _inspect_bundle_file(path: Path, *, mode: str) -> list[RequestCandidate]:
    text = path.read_text(encoding="utf-8")
    return _inspect_bundle_text(text, mode=mode, source=f"bundle:{path.name}", page_url=None)


def _inspect_bundle_text(text: str, *, mode: str, source: str, page_url: str | None) -> list[RequestCandidate]:
    normalized_mode = _normalize_mode(mode)
    candidates: list[RequestCandidate] = []
    if normalized_mode == "handicap":
        query = _extract_best_operation_query(text, operation_name="matchList") or FRONTEND_MATCH_LIST_QUERY
        candidates.append(
            RequestCandidate(
                mode="handicap",
                source=source,
                endpoint_url=HKJC_GRAPHQL_URL,
                method="POST",
                operation_name="matchList",
                request_mode="fallback-json",
                transport_mode="frontend-graphql-document",
                headers=_base_headers(mode="handicap"),
                query=query,
                variables=build_frontend_match_list_variables(page="HDC"),
                referer=page_url or HKJC_HANDICAP_PAGE_URL,
                origin="https://bet.hkjc.com",
                page_url=page_url or HKJC_HANDICAP_PAGE_URL,
                notes=["Extracted from the JCBW main bundle operation document."],
            )
        )
    elif normalized_mode == "results":
        query = _extract_best_operation_query(text, operation_name="matchResults") or FRONTEND_MATCH_RESULTS_QUERY
        candidates.append(
            RequestCandidate(
                mode="results",
                source=source,
                endpoint_url=HKJC_GRAPHQL_URL,
                method="POST",
                operation_name="matchResults",
                request_mode="fallback-json",
                transport_mode="frontend-graphql-document",
                headers=_base_headers(mode="results"),
                query=query,
                variables=build_frontend_match_results_variables(start_date=_today_iso_date(), end_date=_today_iso_date()),
                referer=page_url or HKJC_RESULTS_PAGE_URL,
                origin="https://bet.hkjc.com",
                page_url=page_url or HKJC_RESULTS_PAGE_URL,
                notes=["Extracted from the JCBW main bundle operation document."],
            )
        )
    else:
        query = FRONTEND_MATCH_RESULT_DETAILS_QUERY
        candidates.append(
            RequestCandidate(
                mode="results-detail",
                source=source,
                endpoint_url=HKJC_GRAPHQL_URL,
                method="POST",
                operation_name="matchResultDetails",
                request_mode="fallback-json",
                transport_mode="frontend-graphql-document",
                headers=_base_headers(mode="results-detail"),
                query=query,
                variables=build_frontend_match_result_details_variables(match_id="FB0001"),
                referer=page_url or HKJC_RESULTS_PAGE_URL,
                origin="https://bet.hkjc.com",
                page_url=page_url or HKJC_RESULTS_PAGE_URL,
                notes=["Extracted from the JCBW main bundle operation document."],
            )
        )
    return candidates


def _inspect_html_shell(path: Path) -> list[str]:
    html_text = path.read_text(encoding="utf-8")
    notes: list[str] = []
    main_bundle_url = _extract_bundle_url(html_text, _BUNDLE_MAIN_SRC_RE)
    vendor_bundle_url = _extract_bundle_url(html_text, _BUNDLE_VENDOR_SRC_RE)
    if main_bundle_url:
        notes.append(f"Detected main bundle script: {main_bundle_url}")
    if vendor_bundle_url:
        notes.append(f"Detected vendor bundle script: {vendor_bundle_url}")
    if "GlobalConfig.js" in html_text:
        notes.append("GlobalConfig.js is referenced by the HTML shell.")
    return notes


def _extract_best_operation_query(text: str, *, operation_name: str) -> str | None:
    marker = _OPERATION_MARKERS.get(operation_name)
    if marker is None:
        return None
    positions: list[int] = []
    start = 0
    while True:
        found = text.find(marker, start)
        if found == -1:
            break
        positions.append(found)
        start = found + 1

    decoded_candidates: list[str] = []
    for position in positions:
        end = text.find('",variables', position)
        if end == -1:
            continue
        raw_query = text[position:end]
        decoded_query = raw_query.encode("utf-8").decode("unicode_escape")
        decoded_query = _sanitize_graphql_query(decoded_query)
        if operation_name == "matchList":
            if "currentOdds" not in decoded_query or "foPools(fbOddsTypes: $fbOddsTypes)" not in decoded_query:
                continue
        if operation_name == "matchResults":
            if "matchNumByDate" not in decoded_query or "matches: matchResult" not in decoded_query:
                continue
        if operation_name == "matchResultDetails":
            if "additionalResults" not in decoded_query or "matchResult(matchId: $matchId)" not in decoded_query:
                continue
        decoded_candidates.append(decoded_query.strip())

    if not decoded_candidates:
        return None
    decoded_candidates.sort(key=len)
    return decoded_candidates[0]


def _select_best_candidate(candidates: list[RequestCandidate], *, mode: str) -> RequestCandidate | None:
    matching = [candidate for candidate in candidates if candidate.mode == mode]
    if not matching:
        return None
    scored = sorted(matching, key=lambda candidate: _candidate_score(candidate, mode=mode), reverse=True)
    return scored[0] if scored else None


def _candidate_score(candidate: RequestCandidate, *, mode: str) -> tuple[int, int, int, int]:
    query_text = (candidate.query or "").lower()
    operation = (candidate.operation_name or "").lower()
    variables = candidate.variables or {}
    score = 0

    if candidate.sha256_hash:
        score += 60
    if candidate.query:
        score += 25
    if candidate.variables:
        score += 15
    if "graphql/base" in candidate.endpoint_url:
        score += 20

    if mode == "handicap":
        if "matchlist" in query_text or "matchlist" in operation:
            score += 120
        if "fopools(fboddstypes" in query_text:
            score += 60
        fb_types = variables.get("fbOddsTypes") if isinstance(variables, dict) else None
        if isinstance(fb_types, list) and {"HDC", "EDC"}.issubset({str(item).upper() for item in fb_types}):
            score += 100
    elif mode == "results":
        if "matchresults" in query_text or "matchresults" in operation:
            score += 120
        if "matches: matchresult" in query_text:
            score += 60
        if isinstance(variables, dict) and any(key in variables for key in ("startDate", "endDate", "teamId")):
            score += 40
        if isinstance(variables, dict) and "variables" in variables:
            score -= 30
    else:
        if "matchresultdetails" in query_text or "matchresultdetails" in operation:
            score += 140
        if "additionalresults" in query_text:
            score += 70
        if isinstance(variables, dict) and "matchId" in variables:
            score += 40

    source_bonus = 0
    if "curl:" in candidate.source:
        source_bonus += 3
    if "har:" in candidate.source:
        source_bonus += 2
    if "bundle:" in candidate.source:
        source_bonus += 1
    return (score, len(candidate.query or ""), len(candidate.variables or {}), source_bonus)


def _candidate_json_body(candidate: RequestCandidate) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if candidate.operation_name:
        body["operationName"] = candidate.operation_name
    if candidate.query:
        body["query"] = candidate.query
    if candidate.variables is not None:
        body["variables"] = candidate.variables
    if candidate.extensions is not None:
        body["extensions"] = candidate.extensions
    return body


def _extract_bundle_url(html_text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(html_text)
    if match is None:
        return None
    src = match.group(1)
    if src.startswith("http"):
        return src
    return f"https://bet.hkjc.com{src}"


def _frontend_odds_types(page: str) -> list[str]:
    mapping = {
        "FHA": ["FHA"],
        "HHA": ["HHA", "EHH"],
        "FHH": ["FHH"],
        "HDC": ["HDC", "EDC"],
        "HIL": ["HIL", "EHL"],
        "FHL": ["FHL"],
        "CHL": ["CHL", "ECH"],
        "FHC": ["FHC"],
        "CHD": ["CHD", "ECD"],
        "FCH": ["FCH"],
        "CRS": ["CRS", "ECS"],
        "FCS": ["FCS"],
        "AGS": ["AGS"],
        "LGS": ["LGS"],
        "FGS": ["FGS"],
        "NGS": ["NGS"],
        "MSP": ["MSP"],
        "SGA": ["SGA"],
        "FTS": ["FTS"],
        "NTS": ["NTS", "ENT"],
        "OOE": ["OOE"],
        "TTG": ["TTG", "ETG"],
        "HFT": ["HFT"],
        "OFM": ["HAD", "EHA"],
        "INPLAY": ["HAD", "EHA"],
    }
    return mapping.get(page, ["HAD", "EHA"])


def _frontend_sub_type(page: str) -> int | None:
    mapping = {
        "CHP": 0,
        "WINCNTY": 1,
        "WINCNTT": 2,
        "FINALIST": 3,
    }
    return mapping.get(page)


def _normalize_frontend_match_list_date(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:10].replace("-", "")


def _normalize_frontend_results_date(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Results request date must not be empty.")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return stripped
    if re.fullmatch(r"\d{8}", stripped):
        return f"{stripped[:4]}-{stripped[4:6]}-{stripped[6:8]}"
    raise ValueError("Results request dates must be YYYY-MM-DD or YYYYMMDD.")


def _safe_headers_subset(headers: dict[str, str]) -> dict[str, str]:
    allowed_keys = {
        "Accept",
        "Content-Type",
        "Origin",
        "Referer",
        "User-Agent",
        "apollographql-client-name",
        "apollographql-client-version",
    }
    return {key: value for key, value in headers.items() if key in allowed_keys}


def _base_headers(*, mode: str) -> dict[str, str]:
    headers = dict(_BASE_REQUEST_HEADERS)
    headers["Referer"] = HKJC_HANDICAP_PAGE_URL if _normalize_mode(mode) == "handicap" else HKJC_RESULTS_PAGE_URL
    return headers


def _parse_request_body(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _har_headers_to_dict(raw_headers: Any) -> dict[str, str]:
    if not isinstance(raw_headers, list):
        return {}
    parsed: dict[str, str] = {}
    for item in raw_headers:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if key:
            parsed[key] = value
    return parsed


def _split_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        return "", ""
    key, raw_value = value.split(":", 1)
    return key.strip(), raw_value.strip()


def _mode_from_candidate_hint(*, mode: str, operation_name: str | None, query: str | None, referer: str | None) -> str:
    normalized_mode = _normalize_mode(mode)
    haystack = " ".join(item for item in [operation_name or "", query or "", referer or ""] if item)
    lowered = haystack.lower()
    if "matchresultdetails" in lowered or "results#detail" in lowered:
        return "results-detail"
    if "matchresults" in lowered or "matchresult(" in lowered or "results" in lowered:
        return "results"
    if "matchlist" in lowered or "hdc" in lowered or "football/hdc" in lowered:
        return "handicap"
    return normalized_mode


def _normalize_mode(mode: str) -> str:
    lowered = mode.strip().lower()
    if lowered in {"results-detail", "results_detail", "detail", "result-detail", "result_detail"}:
        return "results-detail"
    return "results" if lowered == "results" else "handicap"


def _infer_row_count(mode: str, response_json: dict[str, Any] | None) -> int:
    if not isinstance(response_json, dict):
        return 0
    data = response_json.get("data")
    if not isinstance(data, dict):
        return 0
    matches = data.get("matches")
    return len(matches) if isinstance(matches, list) else 0


def _normalize_graphql_variables(variables: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(variables, dict):
        return None
    nested = variables.get("variables")
    if isinstance(nested, dict) and set(variables.keys()) == {"variables"}:
        return nested
    return variables


def _decode_cmd_caret_escapes(text: str) -> str:
    # DevTools "Copy as cURL (cmd)" uses caret escapes and line continuations.
    decoded: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "^" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "\r":
                index += 2
                if index < length and text[index] == "\n":
                    index += 1
                decoded.append(" ")
                continue
            if nxt == "\n":
                index += 2
                decoded.append(" ")
                continue
            decoded.append(nxt)
            index += 2
            continue
        decoded.append(ch)
        index += 1
    return "".join(decoded)


def _sanitize_graphql_query(query: str) -> str:
    # Bundle extraction can leave duplicate commas in argument lists.
    sanitized = re.sub(r",\s*,", ", ", query)
    sanitized = re.sub(r"\(\s*,", "(", sanitized)
    sanitized = re.sub(r",\s*\)", ")", sanitized)
    return sanitized


def _object_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _read_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _today_iso_date() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
