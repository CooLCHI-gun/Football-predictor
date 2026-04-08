#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import parse, request


def _read_first_csv_row(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            return {str(k): str(v) for k, v in row.items()}
    return {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pct(value: Any) -> str:
    return f"{_to_float(value) * 100:.2f}%"


def _format_backtest_message(*, run_id: str, summary_path: Path) -> str:
    summary = _read_first_csv_row(summary_path)
    warning = (summary.get("data_source_warning") or "").strip()
    lines = [
        f"[BACKTEST OK] {run_id}",
        f"bets={_to_int(summary.get('total_bets_placed'))}",
        f"win_rate={_pct(summary.get('win_rate'))}",
        f"roi={_pct(summary.get('roi'))}",
        f"max_drawdown={_pct(summary.get('max_drawdown'))}",
        f"summary={summary_path}",
    ]
    if warning:
        lines.append(f"warning={warning}")
    return "\n".join(lines)


def _format_optimize_message(*, run_id: str, best_params_path: Path, params_results_path: Path) -> str:
    best_payload = json.loads(best_params_path.read_text(encoding="utf-8"))
    row_count = 0
    if params_results_path.exists():
        with params_results_path.open("r", encoding="utf-8", newline="") as handle:
            row_count = max(sum(1 for _ in handle) - 1, 0)

    lines = [
        f"[OPTIMIZER OK] {run_id}",
        f"runs={row_count}",
        (
            "best="
            f"edge={_to_float(best_payload.get('min_edge_threshold')):.4f},"
            f"conf={_to_float(best_payload.get('min_confidence_threshold')):.4f},"
            f"max_alerts={_to_int(best_payload.get('max_alerts'))},"
            f"policy={best_payload.get('policy', '')}"
        ),
        f"roi={_pct(best_payload.get('roi'))}",
        f"win_rate={_pct(best_payload.get('win_rate'))}",
        f"max_drawdown={_pct(best_payload.get('max_drawdown'))}",
        f"best={best_params_path}",
    ]
    return "\n".join(lines)


def _format_live_message(*, run_id: str, status_path: Path) -> str:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else {}
    rows = rows if isinstance(rows, dict) else {}
    provider = str(payload.get("provider", ""))
    alerts_mode = str(payload.get("alerts_mode", ""))
    last_success = str(payload.get("last_success_time_utc", ""))
    candidate_rows = _to_int(rows.get("candidate_rows"))
    alerts_sent = _to_int(rows.get("alerts_sent"))

    lines = [
        f"[LIVE OK] {run_id}",
        f"provider={provider}",
        f"alerts_mode={alerts_mode}",
        f"candidate_rows={candidate_rows}",
        f"alerts_sent={alerts_sent}",
        f"last_success_utc={last_success}",
        f"status={status_path}",
    ]
    return "\n".join(lines)


def _send_telegram_message(*, bot_token: str, chat_id: str, message: str) -> None:
    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    req = request.Request(endpoint, data=payload, method="POST")
    with request.urlopen(req, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
    if '"ok":true' not in body.replace(" ", ""):
        raise RuntimeError(f"Telegram API returned non-ok response: {body}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send concise backtest/optimizer/live reports to Telegram.")
    parser.add_argument("--mode", choices=["backtest", "optimize", "live"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--summary-path")
    parser.add_argument("--best-params-path")
    parser.add_argument("--params-results-path")
    parser.add_argument("--live-status-path")
    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        print("telegram secrets not configured; skip report notification")
        return 0

    try:
        if args.mode == "backtest":
            if not args.summary_path:
                raise ValueError("--summary-path is required for mode=backtest")
            summary_path = Path(args.summary_path)
            if not summary_path.exists():
                raise FileNotFoundError(f"summary file not found: {summary_path}")
            message = _format_backtest_message(run_id=args.run_id, summary_path=summary_path)
        elif args.mode == "optimize":
            if not args.best_params_path:
                raise ValueError("--best-params-path is required for mode=optimize")
            if not args.params_results_path:
                raise ValueError("--params-results-path is required for mode=optimize")
            best_params_path = Path(args.best_params_path)
            params_results_path = Path(args.params_results_path)
            if not best_params_path.exists():
                raise FileNotFoundError(f"best params file not found: {best_params_path}")
            message = _format_optimize_message(
                run_id=args.run_id,
                best_params_path=best_params_path,
                params_results_path=params_results_path,
            )
        else:
            if not args.live_status_path:
                raise ValueError("--live-status-path is required for mode=live")
            live_status_path = Path(args.live_status_path)
            if not live_status_path.exists():
                raise FileNotFoundError(f"live status file not found: {live_status_path}")
            message = _format_live_message(run_id=args.run_id, status_path=live_status_path)

        _send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)
    except Exception as exc:  # pragma: no cover - workflow helper script
        print(f"telegram report notification failed: {exc}", file=sys.stderr)
        return 1

    print("telegram report notification sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
