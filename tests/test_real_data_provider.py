from __future__ import annotations

import pandas as pd

from src.providers.football_data_provider import normalize_football_data_frame


def test_normalize_football_data_frame_maps_required_columns() -> None:
    raw_df = pd.DataFrame(
        {
            "Div": ["E0", "E0"],
            "Date": ["11/08/2023", "12/08/2023"],
            "Time": ["20:00", "15:00"],
            "HomeTeam": ["Burnley", "Arsenal"],
            "AwayTeam": ["Man City", "Nott'm Forest"],
            "FTHG": [0, 2],
            "FTAG": [3, 1],
            "AHh": [-1.5, -0.75],
            "B365AHH": [2.01, 1.95],
            "B365AHA": [1.89, 1.97],
        }
    )

    normalized = normalize_football_data_frame(
        raw_df=raw_df,
        source_url="https://www.football-data.co.uk/mmz4281/2324/E0.csv",
        season_code="2324",
        league_code="E0",
    )

    required_columns = {
        "provider_match_id",
        "source_market",
        "competition",
        "season",
        "kickoff_time_utc",
        "home_team_name",
        "away_team_name",
        "ft_home_goals",
        "ft_away_goals",
        "handicap_close_line",
        "odds_home_close",
        "odds_away_close",
        "handicap_side",
    }

    assert len(normalized) == 2
    assert required_columns.issubset(set(normalized.columns))
    assert (normalized["source_market"] == "NON_HKJC").all()
    assert (normalized["handicap_side"] == "home").all()
