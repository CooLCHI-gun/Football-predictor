from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.debug.feature_importance import (
    PROXY_FEATURE_NAMES,
    evaluate_proxy_availability_alerts,
    export_proxy_feature_monitor_report,
)
from src.models.baselines import get_feature_columns


def test_market_feature_columns_include_results_detail_proxies() -> None:
    feature_columns = get_feature_columns(include_market_features=True)
    for name in PROXY_FEATURE_NAMES:
        assert name in feature_columns


def test_export_proxy_feature_monitor_report_outputs_expected_files(tmp_path: Path) -> None:
    feature_frame = pd.DataFrame(
        {
            "rd_pool_available_density": [1.0, 0.8, 0.7, 0.5],
            "rd_combination_available_density": [0.9, 0.8, 0.6, 0.5],
            "rd_combination_suspended_density": [0.1, 0.2, 0.4, 0.5],
            "rd_selection_count_total": [12, 11, 8, 7],
            "rd_unique_selection_count": [10, 9, 8, 7],
            "rd_market_depth_index": [6.0, 5.5, 4.0, 3.5],
        }
    )
    importance_df = pd.DataFrame(
        {
            "feature_name": PROXY_FEATURE_NAMES,
            "importance_value": [0.08, 0.05, 0.04, 0.03, 0.02, 0.01],
            "importance_rank": [5, 8, 11, 15, 17, 22],
            "feature_group": ["market"] * len(PROXY_FEATURE_NAMES),
        }
    )
    kickoff = pd.Series(
        [
            "2026-01-01T12:00:00Z",
            "2026-01-02T12:00:00Z",
            "2026-01-03T12:00:00Z",
            "2026-01-04T12:00:00Z",
        ]
    )

    output_json_path = tmp_path / "proxy_monitor.json"
    output_csv_path = tmp_path / "proxy_monitor.csv"

    json_path, csv_path = export_proxy_feature_monitor_report(
        feature_frame=feature_frame,
        importance_df=importance_df,
        kickoff_series=kickoff,
        output_json_path=output_json_path,
        output_csv_path=output_csv_path,
    )

    assert json_path.exists()
    assert csv_path.exists()

    csv_df = pd.read_csv(csv_path)
    assert set(PROXY_FEATURE_NAMES).issubset(set(csv_df["feature_name"].tolist()))
    assert "drift_ratio" in csv_df.columns
    assert "importance_value" in csv_df.columns


def test_proxy_availability_alert_streak_triggers_on_consecutive_runs(tmp_path: Path) -> None:
    proxy_csv = tmp_path / "proxy_feature_monitor.csv"
    history_json = tmp_path / "proxy_feature_alert_history.json"

    df = pd.DataFrame(
        {
            "feature_name": PROXY_FEATURE_NAMES,
            "importance_value": [0.0] * len(PROXY_FEATURE_NAMES),
            "importance_rank": list(range(1, len(PROXY_FEATURE_NAMES) + 1)),
            "missing_rate": [1.0] * len(PROXY_FEATURE_NAMES),
        }
    )
    df.to_csv(proxy_csv, index=False)

    first = evaluate_proxy_availability_alerts(
        proxy_monitor_csv_path=proxy_csv,
        history_path=history_json,
        missing_rate_threshold=0.9,
        consecutive_runs=2,
        run_tag="run-1",
    )
    second = evaluate_proxy_availability_alerts(
        proxy_monitor_csv_path=proxy_csv,
        history_path=history_json,
        missing_rate_threshold=0.9,
        consecutive_runs=2,
        run_tag="run-2",
    )

    assert first["alert_count"] == 0
    assert second["alert_count"] == len(PROXY_FEATURE_NAMES)
    assert history_json.exists()
