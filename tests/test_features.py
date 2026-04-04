from pathlib import Path
import json

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
                "handicap_line_60m": -0.75,
                "handicap_close_line": -0.5,
                "odds_home_close": 2.00,
                "odds_away_close": 1.85,
                "injury_absence_index_home": 0.20,
                "injury_absence_index_away": 0.10,
                "results_detail_json": json.dumps(
                    {
                        "data": {
                            "matches": [
                                {
                                    "foPools": [
                                        {
                                            "status": "SELLINGSTARTED",
                                            "lines": [
                                                {
                                                    "combinations": [
                                                        {
                                                            "status": "AVAILABLE",
                                                            "selections": [{"selId": "1"}, {"selId": "2"}],
                                                        },
                                                        {
                                                            "status": "SUSPENDED",
                                                            "selections": [{"selId": "3"}],
                                                        },
                                                    ]
                                                }
                                            ],
                                        },
                                        {
                                            "status": "SUSPENDED",
                                            "lines": [
                                                {
                                                    "combinations": [
                                                        {
                                                            "status": "AVAILABLE",
                                                            "selections": [{"selId": "2"}],
                                                        }
                                                    ]
                                                }
                                            ],
                                        },
                                    ]
                                }
                            ]
                        }
                    }
                ),
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
                "handicap_line_60m": -0.25,
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
                "handicap_line_60m": 0.25,
                "handicap_close_line": 0.5,
                "odds_home_close": 2.20,
                "odds_away_close": 1.70,
                "squad_absence_score_home": 0.35,
                "squad_absence_score_away": 0.15,
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


def test_build_features_applies_feature_field_config_missing_strategy(tmp_path: Path) -> None:
    input_path = tmp_path / "matches.csv"
    output_path = tmp_path / "features.csv"
    config_path = tmp_path / "feature_fields.json"

    _synthetic_matches().to_csv(input_path, index=False)
    config_payload = {
        "keep_unlisted_fields": False,
        "active_fields": [
            "provider_match_id",
            "rest_days_home",
            "recent5_hdc_cover_rate_home",
            "missing_home_history_flag",
        ],
        "field_metadata": {
            "rest_days_home": {"missing_strategy": "fill_zero"},
            "recent5_hdc_cover_rate_home": {"missing_strategy": "fill_half"},
            "missing_home_history_flag": {"missing_strategy": "fill_zero"},
        },
    }
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-features",
            "--input-path",
            str(input_path),
            "--output-path",
            str(output_path),
            "--feature-field-config-path",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    output_df = pd.read_csv(output_path)
    assert output_df.columns.tolist() == [
        "provider_match_id",
        "rest_days_home",
        "recent5_hdc_cover_rate_home",
        "missing_home_history_flag",
    ]

    first_row = output_df.iloc[0]
    assert first_row["rest_days_home"] == 0.0
    assert first_row["recent5_hdc_cover_rate_home"] == 0.5


def test_build_features_generates_fixture_density_and_line_drift_60m(tmp_path: Path) -> None:
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
    assert "fixture_density_7d_home" in output_df.columns
    assert "fixture_density_14d_away" in output_df.columns
    assert "line_drift_60m" in output_df.columns

    second_match = output_df[output_df["provider_match_id"] == "m2"].iloc[0]
    assert second_match["fixture_density_7d_home"] == 1
    assert second_match["line_drift_60m"] == 0.25


def test_build_features_fails_fast_on_unknown_feature_field(tmp_path: Path) -> None:
    input_path = tmp_path / "matches.csv"
    output_path = tmp_path / "features.csv"
    config_path = tmp_path / "feature_fields_invalid.json"

    _synthetic_matches().to_csv(input_path, index=False)
    config_payload = {
        "active_fields": ["provider_match_id", "fixture_density_7d_home_typo"],
        "field_metadata": {
            "fixture_density_7d_home_typo": {"missing_strategy": "fill_zero"}
        },
    }
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-features",
            "--input-path",
            str(input_path),
            "--output-path",
            str(output_path),
            "--feature-field-config-path",
            str(config_path),
        ],
    )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "Unknown fields in feature field config" in str(result.exception)


def test_build_features_injury_absence_index_columns_are_generated(tmp_path: Path) -> None:
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
    assert "injury_absence_index_home" in output_df.columns
    assert "injury_absence_index_away" in output_df.columns

    match_m2 = output_df[output_df["provider_match_id"] == "m2"].iloc[0]
    assert match_m2["injury_absence_index_home"] == 0.20
    assert match_m2["injury_absence_index_away"] == 0.10

    match_m3 = output_df[output_df["provider_match_id"] == "m3"].iloc[0]
    assert match_m3["injury_absence_index_home"] == 0.35
    assert match_m3["injury_absence_index_away"] == 0.15


def test_build_features_extracts_results_detail_proxy_features(tmp_path: Path) -> None:
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
    match_m2 = output_df[output_df["provider_match_id"] == "m2"].iloc[0]

    assert match_m2["rd_pool_available_density"] == 0.5
    assert round(float(match_m2["rd_combination_available_density"]), 4) == round(2.0 / 3.0, 4)
    assert round(float(match_m2["rd_combination_suspended_density"]), 4) == round(1.0 / 3.0, 4)
    assert match_m2["rd_selection_count_total"] == 4.0
    assert match_m2["rd_unique_selection_count"] == 3.0
    assert match_m2["rd_market_depth_index"] == 2.0