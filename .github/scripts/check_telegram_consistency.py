#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
REPORT_SCRIPT = REPO_ROOT / ".github" / "scripts" / "send_telegram_report.py"

WORKFLOW_FILES = [
    WORKFLOWS_DIR / "ci.yml",
    WORKFLOWS_DIR / "scheduled-backtest.yml",
    WORKFLOWS_DIR / "scheduled-optimize.yml",
    WORKFLOWS_DIR / "scheduled-live.yml",
    WORKFLOWS_DIR / "pipeline-one-shot.yml",
]


@dataclass(frozen=True)
class CheckIssue:
    file_path: Path
    message: str


def _load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _check_report_script_telegram_labels(script_text: str) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    required_tokens = [
        '"📘 回測摘要通知"',
        '"🧪 優化摘要通知"',
        '"⚽ Live 監測摘要"',
        '"🚨 Workflow 執行失敗"',
        '"⚠️ 僅供研究參考・不構成投注建議"',
    ]
    for token in required_tokens:
        if token not in script_text:
            issues.append(CheckIssue(REPORT_SCRIPT, f"missing required token in report template: {token}"))
    if 'choices=["backtest", "optimize", "live", "failure"]' not in script_text:
        issues.append(CheckIssue(REPORT_SCRIPT, "--mode choices do not include required failure mode"))
    return issues


def _check_workflow_failure_notify(workflow_path: Path, workflow_text: str) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    if "Notify Telegram on failure" not in workflow_text:
        issues.append(CheckIssue(workflow_path, "missing 'Notify Telegram on failure' step"))
    if "python .github/scripts/send_telegram_report.py" not in workflow_text:
        issues.append(CheckIssue(workflow_path, "failure step does not call shared send_telegram_report.py"))
    if "--mode failure" not in workflow_text:
        issues.append(CheckIssue(workflow_path, "failure step missing '--mode failure'"))
    if "https://api.telegram.org/bot" in workflow_text:
        issues.append(CheckIssue(workflow_path, "workflow contains direct Telegram API call; should use shared script"))
    return issues


def _check_plan_a_pipeline_policy(pipeline_text: str) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    if "schedule:" in pipeline_text or "cron:" in pipeline_text:
        issues.append(
            CheckIssue(
                WORKFLOWS_DIR / "pipeline-one-shot.yml",
                "Plan A drift: pipeline-one-shot.yml should not define schedule/cron",
            )
        )
    return issues


def run_checks() -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    report_text = _load_text(REPORT_SCRIPT)
    issues.extend(_check_report_script_telegram_labels(report_text))

    pipeline_text = ""
    for workflow_path in WORKFLOW_FILES:
        workflow_text = _load_text(workflow_path)
        issues.extend(_check_workflow_failure_notify(workflow_path, workflow_text))
        if workflow_path.name == "pipeline-one-shot.yml":
            pipeline_text = workflow_text

    issues.extend(_check_plan_a_pipeline_policy(pipeline_text))
    return issues


def main() -> int:
    try:
        issues = run_checks()
    except Exception as exc:
        print(f"consistency-check failed with unexpected error: {exc}", file=sys.stderr)
        return 2

    if not issues:
        print("telegram consistency check passed")
        return 0

    print("telegram consistency check failed:")
    for issue in issues:
        rel = issue.file_path.relative_to(REPO_ROOT)
        print(f"- {rel}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
