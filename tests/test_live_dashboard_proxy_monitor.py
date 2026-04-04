from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.services.live_runner import LiveRunner


def test_dashboard_renders_proxy_monitor_section(tmp_path: Path) -> None:
    dashboard_path = tmp_path / "dashboard.html"
    odds_history_path = tmp_path / "live_odds_history.csv"
    event_log_path = tmp_path / "live_event_log.csv"
    alert_log_path = tmp_path / "live_alert_log.csv"

    pd.DataFrame(
        [
            {
                "event_time_utc": "2026-04-04T12:00:00Z",
                "event": "cycle_summary",
                "message": "ok",
            }
        ]
    ).to_csv(event_log_path, index=False)
    pd.DataFrame([{"alert_state": "dry_run", "alert_message": "demo"}]).to_csv(alert_log_path, index=False)
    pd.DataFrame([{"provider_match_id": "m1", "odds_home": 1.95}]).to_csv(odds_history_path, index=False)

    status = {
        "phase": "phase6_live_monitoring",
        "mode": "sandbox",
        "mode_separation": {"research": "yes"},
        "rows": {"snapshot_rows": 1},
    }
    snapshot_df = pd.DataFrame([{"provider_match_id": "m1", "competition": "L"}])
    outputs_df = pd.DataFrame([{"provider_match_id": "m1", "model_probability": 0.6}])
    candidates_df = pd.DataFrame([{"provider_match_id": "m1", "edge_score": 0.03}])
    proxy_monitor_df = pd.DataFrame(
        [
            {
                "feature_name": "rd_pool_available_density",
                "importance_value": 0.03,
                "drift_ratio": 0.25,
                "missing_rate": 0.1,
            }
        ]
    )

    LiveRunner._write_dashboard(
        dashboard_path=dashboard_path,
        status=status,
        normalized_snapshot_df=snapshot_df,
        model_outputs_df=outputs_df,
        candidates_df=candidates_df,
        odds_history_path=odds_history_path,
        event_log_path=event_log_path,
        alert_log_path=alert_log_path,
        proxy_monitor_df=proxy_monitor_df,
    )

    html = dashboard_path.read_text(encoding="utf-8")
    assert "Proxy Feature Monitor (Latest)" in html
    assert "rd_pool_available_density" in html
