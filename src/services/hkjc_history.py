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
    raw_market_path: Path
    raw_detail_path: Path
    normalized_output_path: Path
    feature_output_path: Path | None
    result_matches: int
    market_rows: int
    normalized_rows: int
    feature_rows: int | None


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
    provider = HKJCFootballProvider()
    adapter = DefaultHKJCAdapter()

    raw_output_dir.mkdir(parents=True, exist_ok=True)
    normalized_output_path.parent.mkdir(parents=True, exist_ok=True)
    if feature_output_path is not None:
        feature_output_path.parent.mkdir(parents=True, exist_ok=True)

    matches = provider.fetch_results_snapshot(
        start_date=start_date,
        end_date=end_date,
        timeout=timeout_seconds,
    )
    result_rows = _build_result_rows(matches)
    result_df = pd.DataFrame(result_rows)
    raw_results_path = raw_output_dir / "match_results.csv"
    result_df.to_csv(raw_results_path, index=False)

    market_events = provider.fetch_market_snapshot_for_range(
        start_date=start_date,
        end_date=end_date,
        poll_timeout_seconds=timeout_seconds,
    )
    normalized_snapshots = adapter.normalize_batch(market_events)
    market_rows = _build_market_rows(market_events=market_events, normalized_snapshots=normalized_snapshots)
    market_df = pd.DataFrame(market_rows)
    raw_market_path = raw_output_dir / "market_hdc_rows.csv"
    market_df.to_csv(raw_market_path, index=False)

    detail_rows: list[dict[str, Any]] = []
    fb_odds_types = detail_fb_odds_types or ["HDC", "EDC", "HAD", "EHA", "TTG", "ETG", "CHL", "ECH", "CHD", "ECD"]
    for row in result_rows:
        match_id = str(row.get("match_id") or "").strip()
        if not match_id:
            continue
        detail = provider.fetch_result_detail_snapshot(match_id=match_id, timeout=timeout_seconds, fb_odds_types=fb_odds_types)
        detail_rows.append(
            {
                "match_id": match_id,
                "has_detail": bool(isinstance(detail, dict)),
                "detail_json": "" if detail is None else json.dumps(detail, ensure_ascii=False),
            }
        )
    detail_df = pd.DataFrame(detail_rows)
    raw_detail_path = raw_output_dir / "result_details.csv"
    detail_df.to_csv(raw_detail_path, index=False)

    normalized_df = _build_normalized_history_frame(result_df=result_df, market_df=market_df)
    normalized_df.to_csv(normalized_output_path, index=False)

    feature_rows_count: int | None = None
    if feature_output_path is not None:
        build_feature_pipeline(input_path=normalized_output_path, output_path=feature_output_path)
        feature_rows_count = int(len(pd.read_csv(feature_output_path))) if feature_output_path.exists() else 0

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


def _build_market_rows(
    *,
    market_events: list[Any],
    normalized_snapshots: list[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_by_key = {
        str(snapshot.provider_match_id): snapshot
        for snapshot in normalized_snapshots
    }

    for event in market_events:
        payload = event.payload if hasattr(event, "payload") else {}
        provider_match_id = str(payload.get("provider_match_id") or "").strip()
        normalized = normalized_by_key.get(provider_match_id)

        rows.append(
            {
                "match_id": str(payload.get("match_id") or _extract_match_id_from_provider_match_id(provider_match_id)),
                "provider_match_id": provider_match_id,
                "match_number": str(payload.get("match_number") or "").strip(),
                "competition": str(payload.get("competition") or "").strip(),
                "competition_ch": str(payload.get("competition_ch") or "").strip(),
                "home_team_name": str(payload.get("home_team_name") or "").strip(),
                "away_team_name": str(payload.get("away_team_name") or "").strip(),
                "home_team_name_ch": str(payload.get("home_team_name_ch") or "").strip(),
                "away_team_name_ch": str(payload.get("away_team_name_ch") or "").strip(),
                "kickoff_time_utc": str(payload.get("kickoff_time_utc") or "").strip(),
                "snapshot_time_utc": str(payload.get("snapshot_time_utc") or "").strip(),
                "handicap_line": _to_float(payload.get("handicap_line")),
                "odds_home": _to_float(payload.get("odds_home")),
                "odds_away": _to_float(payload.get("odds_away")),
                "line_is_main": bool(payload.get("line_is_main", False)),
                "source_market": "HKJC",
                "normalized_ingestion_key": normalized.ingestion_key() if normalized is not None else "",
            }
        )
    return rows


def _build_normalized_history_frame(result_df: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    if result_df.empty:
        return pd.DataFrame(
            columns=[
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
        )

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


def _extract_match_id_from_provider_match_id(provider_match_id: str) -> str:
    token = provider_match_id.strip()
    if not token:
        return ""
    return token.split("_", 1)[0]


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return None


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
