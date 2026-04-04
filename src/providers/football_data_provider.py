from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd


DEFAULT_FOOTBALL_DATA_URLS = [
    "https://www.football-data.co.uk/mmz4281/2324/E0.csv",
]


@dataclass(frozen=True)
class RealDataImportSummary:
    source_urls: list[str]
    raw_rows_downloaded: int
    normalized_matches_retained: int
    raw_files: list[Path]
    normalized_output_path: Path


def download_and_normalize_football_data(
    urls: list[str],
    raw_output_dir: Path,
    normalized_output_path: Path,
) -> RealDataImportSummary:
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = 0
    normalized_frames: list[pd.DataFrame] = []
    raw_files: list[Path] = []

    for url in urls:
        season_code, league_code = _extract_season_league(url)
        raw_file = raw_output_dir / f"football_data_{season_code}_{league_code}.csv"
        raw_df = pd.read_csv(url)
        raw_df.to_csv(raw_file, index=False)
        raw_files.append(raw_file)
        raw_rows += len(raw_df)

        normalized_frames.append(
            normalize_football_data_frame(
                raw_df=raw_df,
                source_url=url,
                season_code=season_code,
                league_code=league_code,
            )
        )

    normalized_df = pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame()
    normalized_df = normalized_df.sort_values(["kickoff_time_utc", "provider_match_id"]).reset_index(drop=True)

    normalized_output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_df.to_csv(normalized_output_path, index=False)

    return RealDataImportSummary(
        source_urls=urls,
        raw_rows_downloaded=int(raw_rows),
        normalized_matches_retained=int(len(normalized_df)),
        raw_files=raw_files,
        normalized_output_path=normalized_output_path,
    )


def normalize_football_data_frame(
    raw_df: pd.DataFrame,
    source_url: str,
    season_code: str,
    league_code: str,
) -> pd.DataFrame:
    required = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    missing = [column for column in required if column not in raw_df.columns]
    if missing:
        raise ValueError(f"football-data source missing required columns: {', '.join(missing)}")

    frame = raw_df.copy()
    kickoff_series = _parse_kickoff_series(frame)

    handicap_line = _first_available_numeric(frame, ["AHh", "B365AHh", "AvgAHh", "MaxAHh"])
    odds_home_close = _first_available_numeric(frame, ["B365AHH", "PCAHH", "AvgAHH", "MaxAHH"])
    odds_away_close = _first_available_numeric(frame, ["B365AHA", "PCAHA", "AvgAHA", "MaxAHA"])

    normalized = pd.DataFrame(
        {
            "provider_match_id": [f"{league_code}_{season_code}_{idx:04d}" for idx in range(len(frame))],
            "source_market": "NON_HKJC",
            "source_url": source_url,
            "competition": frame.get("Div", pd.Series([league_code] * len(frame))).fillna(league_code),
            "season": season_code,
            "kickoff_time_utc": kickoff_series,
            "home_team_name": frame["HomeTeam"].astype(str),
            "away_team_name": frame["AwayTeam"].astype(str),
            "ft_home_goals": pd.to_numeric(frame["FTHG"], errors="coerce"),
            "ft_away_goals": pd.to_numeric(frame["FTAG"], errors="coerce"),
            "handicap_open_line": handicap_line,
            "handicap_close_line": handicap_line,
            "odds_home_open": odds_home_close,
            "odds_away_open": odds_away_close,
            "odds_home_close": odds_home_close,
            "odds_away_close": odds_away_close,
            "handicap_side": "home",
        }
    )

    return normalized.dropna(
        subset=[
            "kickoff_time_utc",
            "home_team_name",
            "away_team_name",
            "ft_home_goals",
            "ft_away_goals",
        ]
    )


def _parse_kickoff_series(frame: pd.DataFrame) -> pd.Series:
    date_text = frame["Date"].astype(str).str.strip()
    if "Time" in frame.columns:
        time_text = frame["Time"].astype(str).str.strip()
        has_time = ~time_text.isin(["", "nan", "NaN", "None"])
        kickoff_text = date_text.where(~has_time, date_text + " " + time_text)
    else:
        kickoff_text = date_text
    kickoff = pd.to_datetime(kickoff_text, dayfirst=True, utc=True, errors="coerce")
    return kickoff


def _first_available_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    for column in columns:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series([pd.NA] * len(frame), dtype="float64")


def _extract_season_league(url: str) -> tuple[str, str]:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    season_code = "unknown"
    league_code = "unknown"
    if len(path_parts) >= 2:
        season_code = path_parts[-2]
        league_code = Path(path_parts[-1]).stem
    return season_code, league_code