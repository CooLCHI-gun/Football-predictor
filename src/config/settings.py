from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Centralized application settings loaded from .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="dev", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(default="sqlite:///artifacts/hkjc_football.db", alias="DATABASE_URL")
    data_source_tag: str = Field(default="NON_HKJC", alias="DATA_SOURCE_TAG")
    phase2_feature_csv_path: Path = Field(default=Path("data/processed/features_mvp.csv"), alias="PHASE2_FEATURE_CSV_PATH")
    phase3_feature_csv_path: Path = Field(default=Path("data/processed/features_phase3.csv"), alias="PHASE3_FEATURE_CSV_PATH")
    phase3_full_raw_csv_path: Path = Field(
        default=Path("data/raw/real/historical_matches_real_non_hkjc.csv"), alias="PHASE3_FULL_RAW_CSV_PATH"
    )
    phase3_full_feature_csv_path: Path = Field(
        default=Path("data/processed/features_phase3_full.csv"), alias="PHASE3_FULL_FEATURE_CSV_PATH"
    )
    phase3_hkjc_feature_csv_path: Path = Field(
        default=Path("data/processed/features_phase3_hkjc.csv"), alias="PHASE3_HKJC_FEATURE_CSV_PATH"
    )
    feature_field_config_path: Path = Field(default=Path("config/feature_fields.json"), alias="FEATURE_FIELD_CONFIG_PATH")
    backtest_dataset_scope: str = Field(default="AUTO", alias="BACKTEST_DATASET_SCOPE")
    football_data_source_urls: str = Field(
        default="https://www.football-data.co.uk/mmz4281/2324/E0.csv", alias="FOOTBALL_DATA_SOURCE_URLS"
    )

    min_edge_threshold: float = Field(default=0.02, alias="MIN_EDGE_THRESHOLD")
    min_confidence_threshold: float = Field(default=0.56, alias="MIN_CONFIDENCE_THRESHOLD")
    max_concurrent_bets: int = Field(default=5, alias="MAX_CONCURRENT_BETS")
    max_daily_exposure: float = Field(default=0.05, alias="MAX_DAILY_EXPOSURE")
    skip_missing_data: bool = Field(default=True, alias="SKIP_MISSING_DATA")
    flat_stake: float = Field(default=100.0, alias="FLAT_STAKE")

    model_name: str = Field(default="logistic_regression", alias="MODEL_NAME")
    model_approach: str = Field(default="direct_cover", alias="MODEL_APPROACH")
    include_market_features: bool = Field(default=True, alias="INCLUDE_MARKET_FEATURES")
    min_train_matches: int = Field(default=12, alias="MIN_TRAIN_MATCHES")
    walkforward_test_window: int = Field(default=4, alias="WALKFORWARD_TEST_WINDOW")
    retrain_every_matches: int = Field(default=4, alias="RETRAIN_EVERY_MATCHES")
    purge_gap_matches: int = Field(default=0, alias="PURGE_GAP_MATCHES")
    odds_source: str = Field(default="closing", alias="ODDS_SOURCE")
    flip_hkjc_side: bool = Field(default=False, alias="FLIP_HKJC_SIDE")

    bankroll_mode: str = Field(default="fractional_kelly", alias="BANKROLL_MODE")
    bankroll_initial: float = Field(default=10000.0, alias="BANKROLL_INITIAL")
    bankroll_fixed_fraction_pct: float = Field(default=0.01, alias="BANKROLL_FIXED_FRACTION_PCT")
    fractional_kelly_factor: float = Field(default=0.15, alias="FRACTIONAL_KELLY_FACTOR")
    vol_target_rolling_window_bets: int = Field(default=20, alias="VOL_TARGET_ROLLING_WINDOW_BETS")
    vol_target_target_per_bet_vol: float = Field(default=0.03, alias="VOL_TARGET_TARGET_PER_BET_VOL")
    kelly_cap: float = Field(default=0.02, alias="KELLY_CAP")
    bankroll_max_stake_pct: float = Field(default=0.01, alias="BANKROLL_MAX_STAKE_PCT")
    bankroll_min_stake: float = Field(default=10.0, alias="BANKROLL_MIN_STAKE")
    bankroll_daily_max_exposure_pct: float = Field(default=0.03, alias="BANKROLL_DAILY_MAX_EXPOSURE_PCT")
    bankroll_max_drawdown_pct: float = Field(default=0.25, alias="BANKROLL_MAX_DRAWDOWN_PCT")
    bankroll_daily_stop_loss_pct: float | None = Field(default=None, alias="BANKROLL_DAILY_STOP_LOSS_PCT")

    optimizer_lambda_drawdown: float = Field(default=0.5, alias="OPTIMIZER_LAMBDA_DRAWDOWN")
    optimizer_lambda_ror: float = Field(default=0.7, alias="OPTIMIZER_LAMBDA_ROR")
    optimizer_mu_clv: float = Field(default=0.3, alias="OPTIMIZER_MU_CLV")
    optimizer_mu_win_rate: float = Field(default=0.2, alias="OPTIMIZER_MU_WIN_RATE")
    optimizer_mu_placed_bets: float = Field(default=0.2, alias="OPTIMIZER_MU_PLACED_BETS")
    optimizer_target_placed_bets: int = Field(default=120, alias="OPTIMIZER_TARGET_PLACED_BETS")
    optimizer_lambda_low_bets: float = Field(default=0.1, alias="OPTIMIZER_LAMBDA_LOW_BETS")
    optimizer_min_bets_target: int = Field(default=10, alias="OPTIMIZER_MIN_BETS_TARGET")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_dry_run: bool = Field(default=True, alias="TELEGRAM_DRY_RUN")

    proxy_alert_missing_rate_threshold: float = Field(default=0.9, alias="PROXY_ALERT_MISSING_RATE_THRESHOLD")
    proxy_alert_consecutive_runs: int = Field(default=3, alias="PROXY_ALERT_CONSECUTIVE_RUNS")

    @field_validator("bankroll_daily_stop_loss_pct", mode="before")
    @classmethod
    def _normalize_optional_float(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("backtest_dataset_scope", mode="before")
    @classmethod
    def _normalize_dataset_scope(cls, value: object) -> str:
        candidate = str(value).strip().upper() if value is not None else "AUTO"
        allowed = {"AUTO", "NON_HKJC", "HKJC", "MIXED"}
        if candidate not in allowed:
            raise ValueError(f"BACKTEST_DATASET_SCOPE must be one of: {', '.join(sorted(allowed))}")
        return candidate

    @property
    def sqlite_file_path(self) -> Path | None:
        """Return SQLite file path for sqlite:/// URLs, else None."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url[len(prefix) :])
        return None


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
