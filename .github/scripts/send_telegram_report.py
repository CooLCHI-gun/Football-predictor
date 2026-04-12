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


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "nan", "none", "null", "na", "n/a"}:
        return "N/A"
    return text


def _format_backtest_message(*, run_id: str, summary_path: Path) -> str:
    summary = _read_first_csv_row(summary_path)
    raw_warning = str(summary.get("data_source_warning") or "").strip()
    warning = "" if raw_warning.lower() in {"", "nan", "none", "null", "na", "n/a"} else raw_warning
    lines = [
        "📘 回測摘要通知",
        f"🆔 Run ID：{run_id}",
        "━━━━━━━━━━━━",
        f"🎯 下注數：{_to_int(summary.get('total_bets_placed'))}",
        f"✅ 勝率：{_pct(summary.get('win_rate'))}",
        f"💹 ROI：{_pct(summary.get('roi'))}",
        f"📉 最大回撤：{_pct(summary.get('max_drawdown'))}",
        f"📂 摘要檔：{summary_path}",
    ]
    if warning:
        lines.append(f"⚠️ 資料警示：{warning}")
    lines.append("⚠️ 僅供研究參考・不構成投注建議")
    return "\n".join(lines)


def _format_optimize_message(*, run_id: str, best_params_path: Path, params_results_path: Path) -> str:
    best_payload = json.loads(best_params_path.read_text(encoding="utf-8"))
    row_count = 0
    if params_results_path.exists():
        with params_results_path.open("r", encoding="utf-8", newline="") as handle:
            row_count = max(sum(1 for _ in handle) - 1, 0)

    lines = [
        "🧪 優化摘要通知",
        f"🆔 Run ID：{run_id}",
        "━━━━━━━━━━━━",
        f"🔁 測試組合數：{row_count}",
        "🏆 最佳參數",
        f"• Edge 門檻：{_to_float(best_payload.get('min_edge_threshold')):.4f}",
        f"• 信心門檻：{_to_float(best_payload.get('min_confidence_threshold')):.4f}",
        f"• 最大提示數：{_to_int(best_payload.get('max_alerts'))}",
        f"• 策略：{_safe_text(best_payload.get('policy'))}",
        "📊 最佳結果",
        f"• ROI：{_pct(best_payload.get('roi'))}",
        f"• 勝率：{_pct(best_payload.get('win_rate'))}",
        f"• 最大回撤：{_pct(best_payload.get('max_drawdown'))}",
        f"📂 最佳參數檔：{best_params_path}",
        "⚠️ 僅供研究參考・不構成投注建議",
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
        "⚽ Live 監測摘要",
        f"🆔 Run ID：{run_id}",
        "━━━━━━━━━━━━",
        f"📡 Provider：{_safe_text(provider)}",
        f"📬 發送模式：{_safe_text(alerts_mode)}",
        f"🧾 候選賽事：{candidate_rows}",
        f"📨 已發提示：{alerts_sent}",
        f"🕒 最後成功時間（UTC）：{_safe_text(last_success)}",
        f"📂 狀態檔：{status_path}",
        "⚠️ 僅供研究參考・不構成投注建議",
    ]
    return "\n".join(lines)


def _format_failure_message(
    *,
    run_id: str,
    workflow_name: str,
    repository: str,
    branch: str,
    actor: str,
    run_url: str,
) -> str:
    lines = [
        "🚨 Workflow 執行失敗",
        f"🆔 Run ID：{run_id}",
        "━━━━━━━━━━━━",
        f"🛠️ Workflow：{_safe_text(workflow_name)}",
        f"📦 Repo：{_safe_text(repository)}",
        f"🌿 Branch：{_safe_text(branch)}",
        f"👤 Actor：{_safe_text(actor)}",
        f"🔗 Run URL：{_safe_text(run_url)}",
        "請盡快檢查錯誤日誌。",
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
    parser.add_argument("--mode", choices=["backtest", "optimize", "live", "failure"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--summary-path")
    parser.add_argument("--best-params-path")
    parser.add_argument("--params-results-path")
    parser.add_argument("--live-status-path")
    parser.add_argument("--workflow-name")
    parser.add_argument("--repository")
    parser.add_argument("--branch")
    parser.add_argument("--actor")
    parser.add_argument("--run-url")
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
        elif args.mode == "live":
            if not args.live_status_path:
                raise ValueError("--live-status-path is required for mode=live")
            live_status_path = Path(args.live_status_path)
            if not live_status_path.exists():
                raise FileNotFoundError(f"live status file not found: {live_status_path}")
            message = _format_live_message(run_id=args.run_id, status_path=live_status_path)
        else:
            workflow_name = args.workflow_name or os.environ.get("GITHUB_WORKFLOW", "")
            repository = args.repository or os.environ.get("GITHUB_REPOSITORY", "")
            branch = args.branch or os.environ.get("GITHUB_REF_NAME", "")
            actor = args.actor or os.environ.get("GITHUB_ACTOR", "")
            run_url = args.run_url or (
                f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
                f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
            )
            message = _format_failure_message(
                run_id=args.run_id,
                workflow_name=workflow_name,
                repository=repository,
                branch=branch,
                actor=actor,
                run_url=run_url,
            )

        _send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)
    except Exception as exc:  # pragma: no cover - workflow helper script
        print(f"telegram report notification failed: {exc}", file=sys.stderr)
        return 1

    print("telegram report notification sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
