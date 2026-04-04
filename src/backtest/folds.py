from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_end: int
    test_start: int
    test_end: int


def build_walkforward_fold_manifest(
    *,
    total_rows: int,
    min_train_matches: int,
    purge_gap_matches: int,
    walkforward_test_window: int,
    retrain_every_matches: int,
) -> list[WalkForwardFold]:
    """Build deterministic walk-forward fold boundaries used by backtest and optimizer."""
    if total_rows <= 0:
        return []

    fold_manifest: list[WalkForwardFold] = []
    fold_index = 0

    for start in range(min_train_matches, total_rows, retrain_every_matches):
        train_end = start
        test_start = min(train_end + purge_gap_matches, total_rows)
        test_end = min(test_start + walkforward_test_window, total_rows)

        if test_start >= test_end:
            break

        fold_manifest.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        fold_index += 1

    return fold_manifest
