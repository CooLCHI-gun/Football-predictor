from pathlib import Path

import pandas as pd

from src.providers.base import CSVTemplateProvider


class LocalCSVProvider(CSVTemplateProvider):
    """CSV-first MVP provider. Can be replaced by HKJC-specific provider later."""

    def required_columns(self) -> dict[str, list[str]]:
        return {
            "matches_template.csv": [
                "provider_match_id",
                "source_market",
                "competition",
                "season",
                "kickoff_time_utc",
                "home_team_name",
                "away_team_name",
                "ft_home_goals",
                "ft_away_goals",
            ],
            "odds_snapshots_template.csv": [
                "provider_match_id",
                "snapshot_time_utc",
                "bookmaker",
                "source_market",
            ],
            "handicap_lines_template.csv": [
                "provider_match_id",
                "snapshot_time_utc",
                "side",
                "line_value",
                "odds",
                "is_closing_line",
            ],
        }

    def validate_templates(self, base_dir: Path) -> list[str]:
        errors: list[str] = []
        required = self.required_columns()

        for file_name, required_columns in required.items():
            file_path = base_dir / file_name
            if not file_path.exists():
                errors.append(f"Missing template file: {file_name}")
                continue

            df = pd.read_csv(file_path)
            missing_cols = [col for col in required_columns if col not in df.columns]
            if missing_cols:
                errors.append(f"{file_name} missing columns: {', '.join(missing_cols)}")

        return errors
