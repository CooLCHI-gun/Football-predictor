from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.live_feed.providers.hkjc_request_debug import (
    inspect_request_sources,
    report_path_for_mode,
    replay_request_candidate,
    summarize_candidate,
    write_inspection_report,
)
from src.live_feed.providers.hkjc_result_validator import validate_results_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect HKJC frontend request artifacts.")
    parser.add_argument("--mode", choices=["handicap", "results"], default="handicap")
    parser.add_argument("--from-har", type=Path)
    parser.add_argument("--from-curl", type=Path)
    parser.add_argument("--from-bundle", type=Path)
    parser.add_argument("--from-html", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay-live", action="store_true")
    args = parser.parse_args()

    report = inspect_request_sources(
        mode=args.mode,
        from_har=args.from_har,
        from_curl=args.from_curl,
        from_bundle=args.from_bundle,
        from_html=args.from_html,
    )
    output_path = args.output or report_path_for_mode(args.mode)
    write_inspection_report(report, output_path)

    summary = summarize_candidate(report.selected_candidate)
    print(json.dumps({"output_path": str(output_path), "candidate": summary}, ensure_ascii=False, indent=2))

    if args.replay_live and report.selected_candidate is not None:
        replay = replay_request_candidate(report.selected_candidate)
        replay_summary: dict[str, object] = {
            "status_code": replay.get("status_code"),
            "row_count": replay.get("row_count"),
            "response_errors": replay.get("response_errors"),
        }
        if args.mode == "results":
            response_json = replay.get("response_json")
            data = response_json.get("data") if isinstance(response_json, dict) else None
            matches = data.get("matches") if isinstance(data, dict) else None
            if isinstance(matches, list):
                replay_summary["validated_results_preview"] = [
                    item.to_dict() for item in validate_results_snapshot(matches)[:3]
                ]
        print(json.dumps(replay_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
