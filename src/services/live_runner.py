from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import traceback
from typing import Any, Sequence, cast

import pandas as pd

from src.adapters.hkjc.default_adapter import DefaultHKJCAdapter
from src.alerts.notifier import BetRecord, send_bet_alert
from src.alerts.telegram_client import TelegramClient, validate_telegram_configuration
from src.bankroll.models import BankrollState
from src.config.settings import get_settings
from src.live_feed.clients import MarketFeedClient
from src.live_feed.repository import LiveFeedRepository
from src.live_feed.service import LivePredictionService, parse_policy


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveRunSummary:
    cycle_id: str
    mode: str
    provider: str
    run_id: str | None
    output_dir: Path
    snapshot_rows: int
    candidate_rows: int
    alerts_sent: int
    last_success_time_utc: str
    raw_snapshot_path: Path
    odds_history_path: Path
    alert_preview_path: Path | None


@dataclass(frozen=True)
class LiveRunnerConfig:
    provider: str
    model_path: Path
    poll_timeout_seconds: int
    edge_threshold: float
    confidence_threshold: float
    policy: str
    max_alerts: int
    dry_run: bool
    output_dir: Path
    historical_features_path: Path | None = Path("data/processed/features_phase3_full.csv")
    interval_seconds: int = 60
    max_cycles: int | None = None
    continue_on_error: bool = True
    run_id: str | None = None
    flip_hkjc_side: bool = False


class LiveRunner:
    """Orchestrates Phase 6 one-shot and loop cycles with artifact outputs."""

    def __init__(self, *, feed_client: MarketFeedClient, config: LiveRunnerConfig) -> None:
        self._feed_client = feed_client
        self._config = config
        self._adapter = DefaultHKJCAdapter()
        self._ingestion_repository = LiveFeedRepository(self._config.output_dir / "live_ingestion_history.csv")
        settings = get_settings()
        self._bankroll_state = BankrollState(
            initial_bankroll=settings.bankroll_initial,
            current_bankroll=settings.bankroll_initial,
            peak_bankroll=settings.bankroll_initial,
        )

    def run_once(self) -> LiveRunSummary:
        cycle_started = datetime.now(timezone.utc)
        cycle_id = cycle_started.strftime("%Y%m%dT%H%M%SZ")
        mode = "sandbox" if self._config.dry_run else "live"
        LOGGER.info("[Phase6][%s] cycle started provider=%s mode=%s", cycle_id, self._config.provider, mode)

        self._config.output_dir.mkdir(parents=True, exist_ok=True)

        raw_events = self._feed_client.fetch_market_snapshot(
            as_of_utc=cycle_started,
            poll_timeout_seconds=self._config.poll_timeout_seconds,
        )
        raw_snapshot_path = self._persist_raw_snapshot(cycle_id=cycle_id, events=raw_events)
        normalized = self._adapter.normalize_batch(raw_events)
        ingestion_result = self._ingestion_repository.append_snapshots_idempotent(normalized)

        normalized_snapshot_path = self._config.output_dir / "live_snapshot.csv"
        odds_history_path = self._config.output_dir / "live_odds_history.csv"
        normalized_frame = self._build_normalized_snapshot_frame(
            snapshots=normalized,
            fetch_time_utc=cycle_started,
            raw_reference=raw_snapshot_path,
        )
        normalized_frame.to_csv(normalized_snapshot_path, index=False)
        if not normalized_frame.empty:
            history_rows = [
                {str(key): value for key, value in row.items()}
                for row in normalized_frame.to_dict(orient="records")
            ]
            self._append_rows(odds_history_path, history_rows)

        prediction_service = LivePredictionService(
            model_path=self._config.model_path,
            policy=parse_policy(self._config.policy),
            edge_threshold=self._config.edge_threshold,
            confidence_threshold=self._config.confidence_threshold,
            max_alerts=self._config.max_alerts,
            historical_features_path=self._config.historical_features_path,
            flip_hkjc_side=self._config.flip_hkjc_side,
        )
        prediction_result = prediction_service.run(snapshots=normalized, bankroll_state=self._bankroll_state)

        model_outputs_df = prediction_result.snapshot_df
        candidates_df = prediction_result.candidates_df
        if (
            self._config.dry_run
            and self._config.edge_threshold <= 0.0
            and self._config.confidence_threshold <= 0.0
            and candidates_df.empty
            and not model_outputs_df.empty
        ):
            candidates_df = self._build_dry_run_preview_candidates(model_outputs_df)
            LOGGER.info(
                "[Phase6][%s] dry-run preview fallback activated: candidates=%s",
                cycle_id,
                len(candidates_df),
            )
        candidates_path = self._config.output_dir / "live_candidates.csv"
        candidates_df.to_csv(candidates_path, index=False)

        model_outputs_path = self._config.output_dir / "live_model_outputs.csv"
        model_outputs_df.to_csv(model_outputs_path, index=False)

        alert_log_path = self._config.output_dir / "live_alert_log.csv"
        alert_rows = self._send_alerts(cycle_id=cycle_id, candidates_df=candidates_df)
        self._append_rows(alert_log_path, alert_rows)
        alert_preview_path = self._config.output_dir / "live_alert_preview.txt"

        event_log_path = self._config.output_dir / "live_event_log.csv"
        event_rows: list[dict[str, object]] = [
            {
                "cycle_id": cycle_id,
                "event_time_utc": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "event": "cycle_summary",
                "message": (
                    f"raw={len(raw_events)} normalized={len(normalized)} "
                    f"inserted={ingestion_result.inserted_rows} skipped={ingestion_result.skipped_duplicates} "
                    f"candidates={len(candidates_df)}"
                ),
            }
        ]
        self._append_rows(event_log_path, event_rows)

        status = {
            "phase": "phase6_live_monitoring",
            "mode": mode,
            "mode_separation": {
                "research": "backtest/optimize only",
                "sandbox": "live feed + alerts in dry-run mode",
                "live": "explicit opt-in live alerts only; no auto execution",
            },
            "provider": self._config.provider,
            "run_id": self._config.run_id,
            "model_path": str(self._config.model_path),
            "poll_timeout_seconds": self._config.poll_timeout_seconds,
            "interval_seconds": self._config.interval_seconds,
            "raw_snapshot_path": str(raw_snapshot_path),
            "normalized_snapshot_path": str(normalized_snapshot_path),
            "odds_history_path": str(odds_history_path),
            "model_outputs_path": str(model_outputs_path),
            "edge_threshold": self._config.edge_threshold,
            "confidence_threshold": self._config.confidence_threshold,
            "policy": self._config.policy,
            "max_alerts": self._config.max_alerts,
            "flip_hkjc_side": self._config.flip_hkjc_side,
            "alerts_mode": "dry-run" if self._config.dry_run else "live",
            "last_success_time_utc": datetime.now(timezone.utc).isoformat(),
            "cycle_id": cycle_id,
            "rows": {
                "raw_events": len(raw_events),
                "normalized_events": len(normalized),
                "snapshot_rows": int(len(normalized_frame)),
                "model_output_rows": int(len(model_outputs_df)),
                "candidate_rows": int(len(candidates_df)),
                "alerts_sent": int(sum(1 for row in alert_rows if row["alert_state"] == "sent")),
            },
        }
        if self._config.dry_run and alert_preview_path.exists():
            status["alert_preview_path"] = str(alert_preview_path)
        proxy_monitor_path = self._config.output_dir.parent / "debug" / "proxy_feature_monitor.csv"
        status["proxy_monitor_csv_path"] = str(proxy_monitor_path)
        proxy_monitor_df = _safe_read_csv_tail(proxy_monitor_path, rows=12)
        status_path = self._config.output_dir / "live_status.json"
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

        dashboard_path = self._config.output_dir / "dashboard.html"
        self._write_dashboard(
            dashboard_path=dashboard_path,
            status=status,
            normalized_snapshot_df=normalized_frame,
            model_outputs_df=model_outputs_df,
            candidates_df=candidates_df,
            odds_history_path=odds_history_path,
            event_log_path=event_log_path,
            alert_log_path=alert_log_path,
            proxy_monitor_df=proxy_monitor_df,
        )

        LOGGER.info(
            "[Phase6][%s] cycle completed snapshot=%s model_outputs=%s candidates=%s",
            cycle_id,
            len(normalized_frame),
            len(model_outputs_df),
            len(candidates_df),
        )

        return LiveRunSummary(
            cycle_id=cycle_id,
            mode=mode,
            provider=self._config.provider,
            run_id=self._config.run_id,
            output_dir=self._config.output_dir,
            snapshot_rows=int(len(normalized_frame)),
            candidate_rows=int(len(candidates_df)),
            alerts_sent=int(sum(1 for row in alert_rows if row["alert_state"] == "sent")),
            last_success_time_utc=status["last_success_time_utc"],
            raw_snapshot_path=raw_snapshot_path,
            odds_history_path=odds_history_path,
            alert_preview_path=alert_preview_path if self._config.dry_run else None,
        )

    def run_loop(self) -> list[LiveRunSummary]:
        import time

        summaries: list[LiveRunSummary] = []
        cycles = 0
        while True:
            try:
                summary = self.run_once()
                summaries.append(summary)
            except Exception as exc:
                LOGGER.exception("Phase 6 cycle failed: %s", exc)
                self._append_rows(
                    self._config.output_dir / "live_event_log.csv",
                    [
                        {
                            "cycle_id": "error",
                            "event_time_utc": datetime.now(timezone.utc).isoformat(),
                            "level": "ERROR",
                            "event": "cycle_exception",
                            "message": f"{exc}\n{traceback.format_exc()}",
                        }
                    ],
                )
                if not self._config.continue_on_error:
                    raise

            cycles += 1
            if self._config.max_cycles is not None and cycles >= self._config.max_cycles:
                return summaries
            time.sleep(max(1, self._config.interval_seconds))

    def _send_alerts(self, *, cycle_id: str, candidates_df: pd.DataFrame) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        preview_path = self._config.output_dir / "live_alert_preview.txt"
        preview_lines: list[str] = []
        if candidates_df.empty:
            rows.append(
                {
                    "cycle_id": cycle_id,
                    "alert_time_utc": datetime.now(timezone.utc).isoformat(),
                    "provider_match_id": "",
                    "alert_state": "skipped",
                    "message": "No candidates passed thresholds.",
                    "alert_message": "",
                    "mode": "dry-run" if self._config.dry_run else "live",
                }
            )
            if self._config.dry_run:
                preview_path.write_text("No candidates passed thresholds.\n", encoding="utf-8")
            return rows

        settings = get_settings()
        client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=self._config.dry_run,
        )
        validate_telegram_configuration(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=self._config.dry_run,
            source="live-runner",
        )

        alert_log_path = self._config.output_dir / "live_alert_log.csv"
        sent_match_keys_path = self._config.output_dir / "live_sent_match_keys.csv"
        ranked_candidates = candidates_df.copy()
        ranked_candidates["_match_key"] = ranked_candidates.apply(self._build_match_key, axis=1)
        ranked_candidates = ranked_candidates.sort_values(["edge", "confidence_score"], ascending=False)
        ranked_candidates = ranked_candidates.drop_duplicates(subset=["_match_key"], keep="first")

        sent_provider_ids = self._read_sent_provider_match_ids_by_day(alert_log_path)
        sent_match_keys = self._read_sent_match_keys(sent_match_keys_path)
        ranked_candidates["_alert_day"] = ranked_candidates.apply(self._build_alert_day, axis=1)
        ranked_candidates["_dedup_key"] = ranked_candidates["_alert_day"] + "|" + ranked_candidates["_match_key"]
        if "provider_match_id" in ranked_candidates.columns:
            ranked_candidates["_provider_day_key"] = (
                ranked_candidates["_alert_day"] + "|" + ranked_candidates["provider_match_id"].astype(str).str.strip()
            )
        else:
            ranked_candidates["_provider_day_key"] = ranked_candidates["_alert_day"] + "|"
        if sent_provider_ids and "provider_match_id" in ranked_candidates.columns:
            ranked_candidates = ranked_candidates[
                ~ranked_candidates["_provider_day_key"].isin(sent_provider_ids)
            ]
        if sent_match_keys:
            ranked_candidates = ranked_candidates[~ranked_candidates["_dedup_key"].isin(sent_match_keys)]

        for index, (_, row) in enumerate(ranked_candidates.head(self._config.max_alerts).iterrows(), start=1):
            effective_side = str(row.get("effective_predicted_side", row.get("predicted_side", "home")))
            original_side = str(row.get("original_predicted_side", row.get("predicted_side", "home")))
            selected_odds = float(row.get("odds_home_close", 0.0))
            if effective_side == "away":
                selected_odds = float(row.get("odds_away_close", 0.0))
            raw_match_number = str(row.get("match_number", "")).strip()
            if not raw_match_number or raw_match_number.lower() == "nan":
                raw_match_number = str(index)

            bet = BetRecord(
                provider_match_id=str(row.get("provider_match_id", "")),
                kickoff_time_utc=str(row.get("kickoff_time_utc", "")),
                home_team_name=str(row.get("home_team_name", "N/A")),
                away_team_name=str(row.get("away_team_name", "N/A")),
                handicap_line=float(row.get("handicap_close_line", 0.0)),
                model_name=str(row.get("model_name", "live_model")),
                model_approach=str(row.get("model_approach", "direct_cover")),
                predicted_side=effective_side,
                original_predicted_side=original_side,
                predicted_win_probability=float(row.get("model_probability", 0.0)),
                implied_probability=float(row.get("implied_probability", 0.0)),
                edge=float(row.get("edge", 0.0)),
                stake_size=float(row.get("stake_size", 0.0)),
                flip_hkjc_side_enabled=bool(row.get("flip_hkjc_side_enabled", self._config.flip_hkjc_side)),
                confidence_score=float(row.get("confidence_score", 0.0)),
                odds=selected_odds,
                source_label=str(row.get("source_market", self._config.provider)).upper(),
                policy=self._config.policy,
                mode_label="DRY-RUN" if self._config.dry_run else "LIVE",
                competition=str(row.get("competition", "HKJC")),
                competition_zh=str(row.get("competition_ch", "")),
                home_team_name_zh=str(row.get("home_team_name_ch", "")),
                away_team_name_zh=str(row.get("away_team_name_ch", "")),
                market_id=str(row.get("market_id", "ah_ft")),
                match_number=raw_match_number,
                expected_value=float(row.get("expected_value", 0.0)),
            )
            message = send_bet_alert(bet=bet, client=client)
            alert_message = _extract_alert_message(message)
            if self._config.dry_run:
                preview_lines.append(alert_message)
            rows.append(
                {
                    "cycle_id": cycle_id,
                    "alert_time_utc": datetime.now(timezone.utc).isoformat(),
                    "provider_match_id": str(row.get("provider_match_id", "")),
                    "alert_state": "sent",
                    "message": message,
                    "alert_message": alert_message,
                    "mode": "dry-run" if self._config.dry_run else "live",
                }
            )
            self._append_sent_match_key(
                sent_match_keys_path,
                alert_day=str(row.get("_alert_day", "")).strip(),
                match_key=str(row.get("_match_key", "")).strip(),
            )
        if self._config.dry_run:
            preview_blocks: list[str] = []
            for index, preview in enumerate(preview_lines, start=1):
                preview_blocks.append(f"--- Alert {index} ---\n{preview}")
            preview_path.write_text("\n\n".join(preview_blocks) + "\n", encoding="utf-8")
        return rows

    @staticmethod
    def _build_match_key(row: pd.Series) -> str:
        kickoff = str(row.get("kickoff_time_utc", "")).strip().lower()
        home = str(row.get("home_team_name", "")).strip().lower()
        away = str(row.get("away_team_name", "")).strip().lower()
        return f"{kickoff}|{home}|{away}"

    @staticmethod
    def _build_alert_day(row: pd.Series) -> str:
        kickoff_raw = str(row.get("kickoff_time_utc", "")).strip()
        kickoff = pd.to_datetime(kickoff_raw, utc=True, errors="coerce")
        if pd.notna(kickoff):
            return kickoff.date().isoformat()
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _read_sent_provider_match_ids_by_day(alert_log_path: Path) -> set[str]:
        if not alert_log_path.exists():
            return set()
        try:
            alert_log_df = pd.read_csv(alert_log_path)
        except Exception:
            return set()
        if "provider_match_id" not in alert_log_df.columns or "alert_time_utc" not in alert_log_df.columns:
            return set()
        alert_days = pd.to_datetime(alert_log_df["alert_time_utc"], utc=True, errors="coerce").dt.date.astype("string")
        provider_ids = alert_log_df["provider_match_id"].astype("string")
        return {
            f"{str(day).strip()}|{str(provider_id).strip()}"
            for day, provider_id in zip(alert_days.tolist(), provider_ids.tolist())
            if str(day).strip() and str(day).strip().lower() != "nan" and str(provider_id).strip() and str(provider_id).strip().lower() != "nan"
        }

    @staticmethod
    def _read_sent_match_keys(path: Path) -> set[str]:
        if not path.exists():
            return set()
        try:
            frame = pd.read_csv(path)
        except Exception:
            return set()
        if "match_key" not in frame.columns:
            return set()
        if "alert_day" in frame.columns:
            return {
                f"{str(day).strip()}|{str(key).strip()}"
                for day, key in zip(frame["alert_day"].tolist(), frame["match_key"].tolist())
                if str(day).strip() and str(day).strip().lower() != "nan" and str(key).strip() and str(key).strip().lower() != "nan"
            }
        return set()

    @staticmethod
    def _append_sent_match_key(path: Path, *, alert_day: str, match_key: str) -> None:
        day_token = alert_day.strip()
        key = match_key.strip()
        if not day_token or not key:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame(
            [
                {
                    "alert_day": day_token,
                    "match_key": key,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )
        row.to_csv(path, mode="a", index=False, header=not path.exists())

    def _build_dry_run_preview_candidates(self, snapshot_df: pd.DataFrame) -> pd.DataFrame:
        settings = get_settings()
        preview = snapshot_df.copy()
        if preview.empty:
            return preview

        preview = preview.sort_values(["edge", "confidence_score"], ascending=False).head(self._config.max_alerts).copy()
        stake_size = max(float(settings.bankroll_min_stake), float(settings.flat_stake))
        preview["stake_size"] = stake_size
        preview["stake_reason"] = "dry_run_preview_fallback"
        preview["suggested_policy"] = self._config.policy

        expected_values: list[float] = []
        for _, row in preview.iterrows():
            predicted_side = str(row.get("effective_predicted_side", row.get("predicted_side", "home")))
            selected_odds = float(row.get("odds_home_close", 0.0))
            if predicted_side == "away":
                selected_odds = float(row.get("odds_away_close", 0.0))
            probability = float(row.get("model_probability", 0.0))
            expected_roi = probability * (selected_odds - 1.0) - (1.0 - probability)
            expected_values.append(expected_roi * stake_size)
        preview["expected_value"] = expected_values
        return preview

    @staticmethod
    def _append_rows(path: Path, rows: Sequence[dict[str, object]]) -> None:
        if not rows:
            return
        frame = pd.DataFrame(rows)
        write_header = not path.exists()
        frame.to_csv(path, mode="a", index=False, header=write_header)

    def _persist_raw_snapshot(self, *, cycle_id: str, events: Sequence[object]) -> Path:
        raw_dir = self._config.output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        raw_payload: dict[str, object] = {
            "provider": self._config.provider,
            "mode": "dry-run" if self._config.dry_run else "live",
            "cycle_id": cycle_id,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_count": len(events),
            "events": [self._to_raw_event_dict(event) for event in events],
        }

        provider_snapshot_getter = getattr(self._feed_client, "get_last_raw_snapshot", None)
        if callable(provider_snapshot_getter):
            try:
                provider_raw = provider_snapshot_getter()
                if provider_raw is not None:
                    request_meta = provider_raw.get("request_meta") if isinstance(provider_raw, dict) else None
                    response_text = provider_raw.get("response_text") if isinstance(provider_raw, dict) else None
                    provider_debug = provider_raw.get("provider_debug") if isinstance(provider_raw, dict) else None
                    if request_meta is not None:
                        request_meta_path = raw_dir / "latest_request_meta.json"
                        request_meta_path.write_text(
                            json.dumps(request_meta, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    if isinstance(response_text, str):
                        response_text_path = raw_dir / "latest_response_text.txt"
                        response_text_path.write_text(response_text, encoding="utf-8")
                    if provider_debug is not None:
                        provider_debug_path = raw_dir / "latest_provider_debug.json"
                        provider_debug_path.write_text(
                            json.dumps(provider_debug, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                    compact_provider_raw = dict(provider_raw) if isinstance(provider_raw, dict) else {"value": provider_raw}
                    if isinstance(compact_provider_raw, dict):
                        compact_provider_raw.pop("response_text", None)
                    raw_payload["provider_raw"] = compact_provider_raw
            except Exception as exc:
                raw_payload["provider_raw_error"] = str(exc)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        timestamped_path = raw_dir / f"{timestamp}_{self._config.provider}_snapshot.json"
        latest_path = raw_dir / f"latest_raw_{self._config.provider}.json"
        timestamped_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return latest_path

    @staticmethod
    def _to_raw_event_dict(event: object) -> dict[str, object]:
        if hasattr(event, "provider_name") and hasattr(event, "payload"):
            event_obj = cast(object, event)
            provider_name = cast(str, getattr(event_obj, "provider_name", "unknown"))
            payload = cast(dict[str, object], getattr(event_obj, "payload", {}))
            return {"provider_name": provider_name, "payload": payload}
        return {"payload": str(event)}

    def _build_normalized_snapshot_frame(
        self,
        *,
        snapshots: Sequence[object],
        fetch_time_utc: datetime,
        raw_reference: Path,
    ) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for snapshot in snapshots:
            rows.append(self._normalized_row(snapshot=snapshot, fetch_time_utc=fetch_time_utc, raw_reference=raw_reference))

        columns = [
            "fetch_time_utc",
            "provider",
            "provider_match_id",
            "kickoff_time_utc",
            "competition",
            "home_team_name",
            "away_team_name",
            "market_type",
            "handicap_side",
            "handicap_line",
            "odds_home",
            "odds_away",
            "source_market",
            "raw_reference",
            "data_status",
            "parse_status",
        ]
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame(rows)
        return frame.reindex(columns=columns)

    @staticmethod
    def _normalized_row(
        *,
        snapshot: object,
        fetch_time_utc: datetime,
        raw_reference: Path,
    ) -> dict[str, object]:
        return {
            "fetch_time_utc": fetch_time_utc.isoformat(),
            "provider": str(getattr(snapshot, "provider_name", "unknown")),
            "provider_match_id": str(getattr(snapshot, "provider_match_id", "")),
            "kickoff_time_utc": str(getattr(snapshot, "kickoff_time_utc", "")),
            "competition": str(getattr(snapshot, "competition", "")),
            "home_team_name": str(getattr(snapshot, "home_team_name", "")),
            "away_team_name": str(getattr(snapshot, "away_team_name", "")),
            "market_type": "AH",
            "handicap_side": "home",
            "handicap_line": float(getattr(snapshot, "handicap_line", 0.0) or 0.0),
            "odds_home": float(getattr(snapshot, "odds_home", 0.0) or 0.0),
            "odds_away": float(getattr(snapshot, "odds_away", 0.0) or 0.0),
            "source_market": str(getattr(snapshot, "source_market", "")),
            "raw_reference": str(raw_reference),
            "data_status": "ok",
            "parse_status": "ok",
        }

    @staticmethod
    def _write_dashboard(
        *,
        dashboard_path: Path,
        status: dict[str, object],
        normalized_snapshot_df: pd.DataFrame,
        model_outputs_df: pd.DataFrame,
        candidates_df: pd.DataFrame,
        odds_history_path: Path,
        event_log_path: Path,
        alert_log_path: Path,
        proxy_monitor_df: pd.DataFrame,
    ) -> None:
        odds_history_tail = _safe_read_csv_tail(odds_history_path, rows=20)
        recent_events = _safe_read_csv_tail(event_log_path, rows=20)
        recent_alerts = _safe_read_csv_tail(alert_log_path, rows=20)

        def _table_or_empty(frame: pd.DataFrame, empty_message: str) -> str:
            if frame.empty:
                return f"<p class='empty'>{empty_message}</p>"
            return frame.to_html(index=False, classes="data-table")

        mode_separation = cast(dict[str, object], status.get("mode_separation", {}))
        row_summary = cast(dict[str, object], status.get("rows", {}))

        status_lines = "".join(
            f"<li><strong>{key}</strong>: {value}</li>"
            for key, value in status.items()
            if key not in {"mode_separation", "rows"}
        )
        mode_lines = "".join(f"<li><strong>{key}</strong>: {value}</li>" for key, value in mode_separation.items())
        rows_lines = "".join(f"<li><strong>{key}</strong>: {value}</li>" for key, value in row_summary.items())

        html = f"""
<!doctype html>
<html lang='zh-Hant'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Phase 6 Live Monitor</title>
  <style>
    :root {{
      --bg: #f4f8fb;
      --card: #ffffff;
      --ink: #0d2a3d;
      --muted: #5d7688;
      --accent: #0f766e;
      --warn: #b45309;
      --danger: #b91c1c;
      --border: #d8e4ec;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", "Noto Sans TC", sans-serif; background: var(--bg); color: var(--ink); }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 20px; }}
    .hero {{ background: linear-gradient(135deg, #d1fae5, #dbeafe); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; margin-top: 14px; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px; box-shadow: 0 8px 20px rgba(12, 42, 61, 0.06); }}
    h1 {{ margin: 0 0 6px 0; font-size: 1.4rem; }}
    h2 {{ margin: 0 0 10px 0; font-size: 1.05rem; }}
    p {{ margin: 4px 0; color: var(--muted); }}
    ul {{ margin: 0; padding-left: 18px; }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    .data-table th, .data-table td {{ border: 1px solid var(--border); padding: 6px; text-align: left; }}
    .data-table thead th {{ background: #e2e8f0; position: sticky; top: 0; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    .footer {{ margin-top: 12px; font-size: 0.8rem; color: var(--muted); }}
  </style>
</head>
<body>
  <div class='wrap'>
    <section class='hero'>
      <h1>HKJC Football Phase 6 Live Monitor</h1>
      <p>Research-first sandbox dashboard. No automatic execution.</p>
    </section>

    <div class='grid'>
      <section class='card'>
        <h2>System Status</h2>
        <ul>{status_lines}</ul>
      </section>
      <section class='card'>
        <h2>Mode Separation</h2>
        <ul>{mode_lines}</ul>
      </section>
      <section class='card'>
        <h2>Cycle Counters</h2>
        <ul>{rows_lines}</ul>
      </section>
    </div>

    <div class='grid'>
      <section class='card'>
        <h2>Upcoming / Live Snapshot</h2>
                {_table_or_empty(normalized_snapshot_df, "No snapshot rows yet")}
      </section>
      <section class='card'>
                <h2>Model Outputs</h2>
                {_table_or_empty(model_outputs_df, "No model outputs yet")}
            </section>
        </div>

        <div class='grid'>
            <section class='card'>
                <h2>Candidate Alerts</h2>
        {_table_or_empty(candidates_df, "No candidates passed filters")}
            </section>
            <section class='card'>
                <h2>Recent Odds History Sample</h2>
                {_table_or_empty(odds_history_tail, "No odds history rows yet")}
      </section>
    </div>

    <div class='grid'>
      <section class='card'>
        <h2>Alert Log</h2>
        {_table_or_empty(recent_alerts, "No alert log rows")}
      </section>
      <section class='card'>
        <h2>Event Log</h2>
        {_table_or_empty(recent_events, "No event log rows")}
      </section>
    </div>

        <div class='grid'>
            <section class='card'>
                <h2>Proxy Feature Monitor (Latest)</h2>
                {_table_or_empty(proxy_monitor_df, "No proxy monitor rows yet; run train to generate artifacts/debug/proxy_feature_monitor.csv")}
            </section>
        </div>

    <p class='footer'>Generated from artifacts/live CSV and JSON outputs.</p>
  </div>
</body>
</html>
"""
        dashboard_path.write_text(html.strip() + "\n", encoding="utf-8")


def _extract_alert_message(send_result: str) -> str:
    prefix = "DRY_RUN: "
    if send_result.startswith(prefix):
        return send_result[len(prefix) :]
    return send_result


def _safe_read_csv_tail(path: Path, *, rows: int) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path).tail(rows)
    except Exception:
        return pd.DataFrame()
