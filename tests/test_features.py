from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from src.main import app


def _synthetic_matches() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "provider_match_id": "m2",
                "kickoff_time_utc": "2024-01-05T12:00:00Z",
                "competition": "LeagueA",
                "home_team_name": "A",
                "away_team_name": "C",
                "ft_home_goals": 0,
                "ft_away_goals": 0,
                "handicap_open_line": -0.5,
                "handicap_close_line": -0.5,
                "odds_home_close": 2.00,
                "odds_away_close": 1.85,
            },
            {
                "provider_match_id": "m1",
                "kickoff_time_utc": "2024-01-01T12:00:00Z",
                "competition": "LeagueA",
                "home_team_name": "A",
                "away_team_name": "B",
                "ft_home_goals": 2,
                "ft_away_goals": 1,
                "handicap_open_line": -0.25,
                "handicap_close_line": -0.5,
                "odds_home_close": 1.90,
                "odds_away_close": 1.95,
            },
            {
                "provider_match_id": "m3",
                "kickoff_time_utc": "2024-01-10T12:00:00Z",
                "competition": "LeagueA",
                "home_team_name": "B",
                "away_team_name": "A",
                "ft_home_goals": 1,
                "ft_away_goals": 3,
                "handicap_open_line": 0.25,
                "handicap_close_line": 0.5,
                "odds_home_close": 2.20,
                "odds_away_close": 1.70,
            },
        ]
    )


def test_feature_pipeline_orders_matches_by_kickoff_time(tmp_path: Path) -> None:
    input_path = tmp_path / "matches.csv"
    output_path = tmp_path / "features.csv"
    _synthetic_matches().to_csv(input_path, index=False)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-features",
            "--input-path",
            str(input_path),
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    output_df = pd.read_csv(output_path)
    assert output_df["provider_match_id"].tolist() == ["m1", "m2", "m3"]


def test_feature_pipeline_has_no_data_leakage(tmp_path: Path) -> None:
    input_path = tmp_path / "matches.csv"
    output_path = tmp_path / "features.csv"
    _synthetic_matches().to_csv(input_path, index=False)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-features",
            "--input-path",
            str(input_path),
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    output_df = pd.read_csv(output_path)

    first_match = output_df[output_df["provider_match_id"] == "m1"].iloc[0]
    second_match = output_df[output_df["provider_match_id"] == "m2"].iloc[0]
    third_match = output_df[output_df["provider_match_id"] == "m3"].iloc[0]

    assert first_match["history_home_matches_count"] == 0
    assert first_match["history_away_matches_count"] == 0
    assert first_match["home_form_points_last5"] == 0

    assert second_match["history_home_matches_count"] == 1
    assert second_match["home_form_points_last5"] == 3
    assert second_match["home_goals_scored_last5"] == 2
    assert second_match["home_goals_conceded_last5"] == 1

    assert third_match["history_away_matches_count"] == 2
    assert third_match["away_form_points_last5"] == 4


def test_build_features_cli_outputs_expected_columns_and_row_count(tmp_path: Path) -> None:
    input_path = tmp_path / "matches.csv"
    output_path = tmp_path / "features.csv"
    _synthetic_matches().to_csv(input_path, index=False)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-features",
            "--input-path",
            str(input_path),
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()

    output_df = pd.read_csv(output_path)
    expected_columns = {
        "provider_match_id",
        "kickoff_time_utc",
        "competition",
        "home_team_name",
        "away_team_name",
        "ft_home_goals",
        "ft_away_goals",
        "handicap_close_line",
        "odds_home_close",
        "target_handicap_side",
        "home_form_points_last5",
        "away_form_points_last5",
        "home_goals_scored_last5",
        "away_goals_scored_last5",
        "rest_days_home",
        "rest_days_away",
        "elo_home_pre",
        "elo_away_pre",
        "elo_diff_pre",
        "handicap_line_movement",
        "implied_prob_home_close",
        "implied_prob_away_close",
        "missing_odds_flag",
    }

    assert len(output_df) == 3
    assert expected_columns.issubset(set(output_df.columns))