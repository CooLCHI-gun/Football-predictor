# Balanced ROI/Win-Rate Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded balanced optimizer mode that favors positive-ROI, stable parameter sets with acceptable drawdown and sufficient outer-window bet coverage.

**Architecture:** Keep the existing optimizer grid search and walk-forward backtest flow, but extend outer-window aggregation with stability metrics and route `OPTIMIZER_MODE=BALANCED_GUARDED` through a new scoring branch. Expose the new thresholds and penalty weights through `AppSettings` so CLI workflows, `.env`, and scheduled jobs can tune the mode without further code edits.

**Tech Stack:** Python 3.11, pandas, pydantic-settings, pytest, Typer CLI

---

## File map

- Modify: `src\config\settings.py` — define new optimizer env settings and allow the new mode.
- Modify: `src\optimizer\grid_search.py` — compute stability metrics, add balanced-guarded scoring, and persist new artifact fields.
- Modify: `tests\test_optimizer.py` — cover new gates, stability-aware ranking, and fallback artifact behavior.
- Reference: `docs\superpowers\specs\2026-04-15-football-roi-design.md` — approved design spec for this work.

### Task 1: Lock in expected behavior with failing optimizer tests

**Files:**
- Modify: `tests\test_optimizer.py`
- Reference: `src\optimizer\grid_search.py`

- [ ] **Step 1: Add a failing test for balanced-guarded gates and stability ranking**

```python
def test_optimizer_balanced_guarded_prefers_stable_positive_roi_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature_path = tmp_path / "features_balanced.csv"
    output_dir = tmp_path / "optimizer"
    pd.DataFrame({"row_id": list(range(240))}).to_csv(feature_path, index=False)

    window_profiles = {
        0.01: [
            {"roi": 0.06, "win_rate": 0.59, "max_drawdown": 0.09, "total_bets_placed": 118},
            {"roi": -0.01, "win_rate": 0.50, "max_drawdown": 0.10, "total_bets_placed": 115},
            {"roi": 0.07, "win_rate": 0.60, "max_drawdown": 0.11, "total_bets_placed": 116},
        ],
        0.02: [
            {"roi": 0.045, "win_rate": 0.57, "max_drawdown": 0.08, "total_bets_placed": 126},
            {"roi": 0.042, "win_rate": 0.56, "max_drawdown": 0.09, "total_bets_placed": 124},
            {"roi": 0.044, "win_rate": 0.58, "max_drawdown": 0.09, "total_bets_placed": 125},
        ],
    }

    def _fake_run_backtest_with_result(
        *,
        input_path: Path,
        strategy_overrides: dict[str, object],
        **_: object,
    ) -> SimpleNamespace:
        edge = float(strategy_overrides["min_edge_threshold"])
        token = input_path.name.removeprefix("outer_window_").removesuffix(".csv")
        window_idx = max(0, int(token) - 1) if input_path.name.startswith("outer_window_") else 0
        summary = {
            **window_profiles[edge][window_idx],
            "risk_of_ruin_estimate": 0.05,
            "avg_clv_pct": 0.0,
            "median_clv_pct": 0.0,
            "pct_positive_clv": 0.0,
            "prediction_cache_hits": 0,
            "prediction_cache_misses": 1,
        }
        return SimpleNamespace(summary=summary)

    monkeypatch.setattr("src.optimizer.grid_search.run_backtest_with_result", _fake_run_backtest_with_result)
    monkeypatch.setenv("OPTIMIZER_MODE", "BALANCED_GUARDED")
    monkeypatch.setenv("OPTIMIZER_HARD_MIN_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_WINDOW_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_DRAWDOWN_CAP", "0.12")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_ROI", "0.0")
    get_settings.cache_clear()

    try:
        optimize_strategy(
            input_path=feature_path,
            output_dir=output_dir,
            edge_grid=[0.01, 0.02],
            confidence_grid=[0.55],
            policy_grid=["flat"],
            kelly_grid=[0.15],
            max_alerts_grid=[1],
            max_stake_grid=[0.01],
            daily_exposure_grid=[0.03],
            outer_rolling_windows=3,
            outer_min_window_matches=60,
            max_runs=2,
        )
    finally:
        get_settings.cache_clear()

    best_payload = json.loads((output_dir / "best_params.json").read_text(encoding="utf-8"))
    assert float(best_payload["min_edge_threshold"]) == 0.02
    assert float(best_payload["worst_window_roi"]) > 0.0
    assert float(best_payload["roi_std"]) < 0.01
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests\test_optimizer.py::test_optimizer_balanced_guarded_prefers_stable_positive_roi_profile -v`

Expected: FAIL because `BALANCED_GUARDED` and the new artifact fields are not implemented yet.

- [ ] **Step 3: Add a failing test for the fallback winner note when all runs fail guards**

```python
def test_optimizer_balanced_guarded_marks_fallback_winner_when_all_runs_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature_path = tmp_path / "features_fallback.csv"
    output_dir = tmp_path / "optimizer"
    pd.DataFrame({"row_id": list(range(180))}).to_csv(feature_path, index=False)

    def _fake_run_backtest_with_result(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            summary={
                "roi": -0.02,
                "win_rate": 0.52,
                "max_drawdown": 0.15,
                "total_bets_placed": 88,
                "risk_of_ruin_estimate": 0.08,
                "avg_clv_pct": 0.0,
                "median_clv_pct": 0.0,
                "pct_positive_clv": 0.0,
                "prediction_cache_hits": 0,
                "prediction_cache_misses": 1,
            }
        )

    monkeypatch.setattr("src.optimizer.grid_search.run_backtest_with_result", _fake_run_backtest_with_result)
    monkeypatch.setenv("OPTIMIZER_MODE", "BALANCED_GUARDED")
    monkeypatch.setenv("OPTIMIZER_HARD_MIN_BETS", "50")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_WINDOW_BETS", "100")
    monkeypatch.setenv("OPTIMIZER_BALANCED_DRAWDOWN_CAP", "0.12")
    monkeypatch.setenv("OPTIMIZER_BALANCED_MIN_ROI", "0.0")
    get_settings.cache_clear()

    try:
        optimize_strategy(
            input_path=feature_path,
            output_dir=output_dir,
            edge_grid=[0.01],
            confidence_grid=[0.55],
            policy_grid=["flat"],
            kelly_grid=[0.15],
            max_alerts_grid=[1],
            max_stake_grid=[0.01],
            daily_exposure_grid=[0.03],
            outer_rolling_windows=1,
            outer_min_window_matches=60,
            max_runs=1,
        )
    finally:
        get_settings.cache_clear()

    best_payload = json.loads((output_dir / "best_params.json").read_text(encoding="utf-8"))
    assert best_payload["selection_reason"] == "fallback_best_score"
    assert best_payload["passed_guardrails"] is False
```

- [ ] **Step 4: Run the fallback test to verify it fails**

Run: `pytest tests\test_optimizer.py::test_optimizer_balanced_guarded_marks_fallback_winner_when_all_runs_fail -v`

Expected: FAIL because `selection_reason` and `passed_guardrails` do not exist yet.

- [ ] **Step 5: Commit the failing-test checkpoint**

```powershell
git add tests\test_optimizer.py
git commit -m "test: define balanced guarded optimizer expectations"
```

### Task 2: Add configuration for balanced-guarded optimizer mode

**Files:**
- Modify: `src\config\settings.py`
- Test: `tests\test_optimizer.py`

- [ ] **Step 1: Extend settings with the new mode and env-backed thresholds**

```python
    optimizer_mode: str = Field(default="BALANCED", alias="OPTIMIZER_MODE")
    optimizer_hard_min_bets: int = Field(default=120, alias="OPTIMIZER_HARD_MIN_BETS")
    optimizer_winrate_min_win_rate: float = Field(default=0.53, alias="OPTIMIZER_WINRATE_MIN_WIN_RATE")
    optimizer_winrate_drawdown_cap: float = Field(default=0.12, alias="OPTIMIZER_WINRATE_DRAWDOWN_CAP")
    optimizer_balanced_drawdown_cap: float = Field(default=0.12, alias="OPTIMIZER_BALANCED_DRAWDOWN_CAP")
    optimizer_balanced_min_window_bets: int = Field(default=100, alias="OPTIMIZER_BALANCED_MIN_WINDOW_BETS")
    optimizer_balanced_min_roi: float = Field(default=0.0, alias="OPTIMIZER_BALANCED_MIN_ROI")
    optimizer_balanced_lambda_roi_std: float = Field(default=0.35, alias="OPTIMIZER_BALANCED_LAMBDA_ROI_STD")
    optimizer_balanced_lambda_win_rate_std: float = Field(default=0.25, alias="OPTIMIZER_BALANCED_LAMBDA_WIN_RATE_STD")
    optimizer_balanced_lambda_worst_window_roi: float = Field(default=0.30, alias="OPTIMIZER_BALANCED_LAMBDA_WORST_WINDOW_ROI")
```

- [ ] **Step 2: Update optimizer mode validation**

```python
    @field_validator("optimizer_mode", mode="before")
    @classmethod
    def _normalize_optimizer_mode(cls, value: object) -> str:
        candidate = str(value).strip().upper() if value is not None else "BALANCED"
        allowed = {"BALANCED", "BALANCED_GUARDED", "WINRATE_GUARDED"}
        if candidate not in allowed:
            raise ValueError(f"OPTIMIZER_MODE must be one of: {', '.join(sorted(allowed))}")
        return candidate
```

- [ ] **Step 3: Run the targeted tests that depend on the new mode parsing**

Run: `pytest tests\test_optimizer.py::test_optimizer_balanced_guarded_prefers_stable_positive_roi_profile tests\test_optimizer.py::test_optimizer_balanced_guarded_marks_fallback_winner_when_all_runs_fail -v`

Expected: FAIL, but no longer because `OPTIMIZER_MODE=BALANCED_GUARDED` is rejected.

- [ ] **Step 4: Commit the settings checkpoint**

```powershell
git add src\config\settings.py tests\test_optimizer.py
git commit -m "feat: add balanced guarded optimizer settings"
```

### Task 3: Implement balanced-guarded scoring and outer-window stability metrics

**Files:**
- Modify: `src\optimizer\grid_search.py`
- Test: `tests\test_optimizer.py`

- [ ] **Step 1: Extend `ObjectiveWeights` with balanced-guarded fields**

```python
@dataclass(frozen=True)
class ObjectiveWeights:
    lambda_drawdown: float
    lambda_ror: float
    mu_clv: float
    mu_win_rate: float
    mu_placed_bets: float
    target_placed_bets: int
    lambda_low_bets: float
    min_bets_target: int
    mode: str = "BALANCED"
    winrate_min_win_rate: float = 0.53
    winrate_drawdown_cap: float = 0.12
    hard_min_bets: int = 120
    balanced_drawdown_cap: float = 0.12
    balanced_min_window_bets: int = 100
    balanced_min_roi: float = 0.0
    balanced_lambda_roi_std: float = 0.35
    balanced_lambda_win_rate_std: float = 0.25
    balanced_lambda_worst_window_roi: float = 0.30
```

- [ ] **Step 2: Pass the new settings into `ObjectiveWeights(...)`**

```python
    weights = ObjectiveWeights(
        lambda_drawdown=settings.optimizer_lambda_drawdown,
        lambda_ror=float(getattr(settings, "optimizer_lambda_ror", 0.7)),
        mu_clv=float(getattr(settings, "optimizer_mu_clv", 0.3)),
        mu_win_rate=float(getattr(settings, "optimizer_mu_win_rate", 0.2)),
        mu_placed_bets=float(getattr(settings, "optimizer_mu_placed_bets", 0.2)),
        target_placed_bets=int(getattr(settings, "optimizer_target_placed_bets", 120)),
        lambda_low_bets=settings.optimizer_lambda_low_bets,
        min_bets_target=settings.optimizer_min_bets_target,
        mode=str(getattr(settings, "optimizer_mode", "BALANCED")).upper(),
        winrate_min_win_rate=float(getattr(settings, "optimizer_winrate_min_win_rate", 0.53)),
        winrate_drawdown_cap=float(getattr(settings, "optimizer_winrate_drawdown_cap", 0.12)),
        hard_min_bets=int(getattr(settings, "optimizer_hard_min_bets", 120)),
        balanced_drawdown_cap=float(getattr(settings, "optimizer_balanced_drawdown_cap", 0.12)),
        balanced_min_window_bets=int(getattr(settings, "optimizer_balanced_min_window_bets", 100)),
        balanced_min_roi=float(getattr(settings, "optimizer_balanced_min_roi", 0.0)),
        balanced_lambda_roi_std=float(getattr(settings, "optimizer_balanced_lambda_roi_std", 0.35)),
        balanced_lambda_win_rate_std=float(getattr(settings, "optimizer_balanced_lambda_win_rate_std", 0.25)),
        balanced_lambda_worst_window_roi=float(getattr(settings, "optimizer_balanced_lambda_worst_window_roi", 0.30)),
    )
```

- [ ] **Step 3: Return stability metrics from outer rolling aggregation**

```python
    return {
        "roi": statistics.fmean(roi_values) if roi_values else 0.0,
        "win_rate": statistics.fmean(win_rate_values) if win_rate_values else 0.0,
        "max_drawdown": max(drawdown_values) if drawdown_values else 0.0,
        "total_bets_placed": int(round(statistics.fmean(bets_values))) if bets_values else 0,
        "min_window_bets": min(bets_values) if bets_values else 0,
        "worst_window_roi": min(roi_values) if roi_values else 0.0,
        "worst_window_win_rate": min(win_rate_values) if win_rate_values else 0.0,
        "roi_std": statistics.pstdev(roi_values) if len(roi_values) > 1 else 0.0,
        "win_rate_std": statistics.pstdev(win_rate_values) if len(win_rate_values) > 1 else 0.0,
        "risk_of_ruin_estimate": max(ror_values) if ror_values else None,
        "avg_clv_pct": statistics.fmean(clv_avg_values) if clv_avg_values else None,
        "median_clv_pct": statistics.fmean(clv_median_values) if clv_median_values else None,
        "pct_positive_clv": statistics.fmean(positive_clv_values) if positive_clv_values else None,
        "prediction_cache_hits": sum(_as_int(item.get("prediction_cache_hits"), 0) for item in summaries),
        "prediction_cache_misses": sum(_as_int(item.get("prediction_cache_misses"), 0) for item in summaries),
        "outer_rolling_windows": len(summaries),
    }
```

- [ ] **Step 4: Persist the new metrics into each optimizer result row**

```python
        worst_window_roi = _as_float(summary.get("worst_window_roi"), roi)
        worst_window_win_rate = _as_float(summary.get("worst_window_win_rate"), win_rate)
        roi_std = _as_float(summary.get("roi_std"), 0.0)
        win_rate_std = _as_float(summary.get("win_rate_std"), 0.0)

        row: dict[str, object] = {
            **asdict(params),
            "roi": roi,
            "max_drawdown": max_drawdown,
            "risk_of_ruin_estimate": risk_of_ruin_estimate,
            "total_bets_placed": total_bets,
            "min_window_bets": min_window_bets,
            "win_rate": win_rate,
            "worst_window_roi": worst_window_roi,
            "worst_window_win_rate": worst_window_win_rate,
            "roi_std": roi_std,
            "win_rate_std": win_rate_std,
            "avg_clv_pct": avg_clv_pct,
            "median_clv_pct": median_clv_pct,
            "pct_positive_clv": pct_positive_clv,
            "clv_score": clv_score,
            "outer_rolling_windows": _as_int(summary.get("outer_rolling_windows"), 1),
            "outer_min_window_matches": outer_min_window_matches,
            "prediction_cache_hits": run_cache_hits,
            "prediction_cache_misses": run_cache_misses,
            "score": score,
        }
```

- [ ] **Step 5: Add the balanced-guarded scoring branch**

```python
def compute_objective_score(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    min_window_bets: int | None = None,
    worst_window_roi: float | None = None,
    roi_std: float = 0.0,
    win_rate_std: float = 0.0,
    weights: ObjectiveWeights,
) -> float:
    min_window_bets = total_bets_placed if min_window_bets is None else min_window_bets
    if min_window_bets < weights.hard_min_bets:
        return -1000.0 - (weights.hard_min_bets - min_window_bets)

    if weights.mode == "BALANCED_GUARDED":
        return compute_objective_score_balanced_guarded(
            roi=roi,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            risk_of_ruin_estimate=risk_of_ruin_estimate,
            clv_score=clv_score,
            total_bets_placed=total_bets_placed,
            min_window_bets=min_window_bets,
            worst_window_roi=worst_window_roi if worst_window_roi is not None else roi,
            roi_std=roi_std,
            win_rate_std=win_rate_std,
            weights=weights,
        )
```

- [ ] **Step 6: Implement the balanced-guarded score function**

```python
def compute_objective_score_balanced_guarded(
    *,
    roi: float,
    win_rate: float,
    max_drawdown: float,
    risk_of_ruin_estimate: float | None,
    clv_score: float | None,
    total_bets_placed: int,
    min_window_bets: int,
    worst_window_roi: float,
    roi_std: float,
    win_rate_std: float,
    weights: ObjectiveWeights,
) -> float:
    if max_drawdown > weights.balanced_drawdown_cap:
        return -600.0 - (max_drawdown - weights.balanced_drawdown_cap) * 100.0
    if min_window_bets < weights.balanced_min_window_bets:
        return -500.0 - (weights.balanced_min_window_bets - min_window_bets)
    if roi <= weights.balanced_min_roi:
        return -400.0 - abs(roi - weights.balanced_min_roi) * 100.0

    risk_penalty = risk_of_ruin_estimate if risk_of_ruin_estimate is not None else 1.0
    clv_term = clv_score if clv_score is not None else 0.0
    placed_bets_term = min(1.0, total_bets_placed / max(1, weights.target_placed_bets))

    return (
        roi
        + (weights.mu_win_rate * win_rate)
        + (weights.mu_placed_bets * placed_bets_term)
        + (weights.mu_clv * clv_term)
        - (weights.lambda_drawdown * max_drawdown)
        - (weights.lambda_ror * risk_penalty)
        - (weights.balanced_lambda_roi_std * roi_std)
        - (weights.balanced_lambda_win_rate_std * win_rate_std)
        + (weights.balanced_lambda_worst_window_roi * worst_window_roi)
    )
```

- [ ] **Step 7: Mark fallback winners in `best_params.json`**

```python
    eligible_for_best = (
        _as_int(row.get("min_window_bets"), 0) >= weights.hard_min_bets
        and (
            weights.mode != "BALANCED_GUARDED"
            or (
                _as_float(row.get("max_drawdown"), 0.0) <= weights.balanced_drawdown_cap
                and _as_float(row.get("roi"), 0.0) > weights.balanced_min_roi
                and _as_int(row.get("min_window_bets"), 0) >= weights.balanced_min_window_bets
            )
        )
    )

    if best_row is None and rows:
        best_row = max(rows, key=lambda item: _as_float(item.get("score"), -1e9))
        best_row = {
            **best_row,
            "selection_reason": "fallback_best_score",
            "passed_guardrails": False,
        }
    elif best_row is not None:
        best_row = {
            **best_row,
            "selection_reason": "guardrail_best_score",
            "passed_guardrails": True,
        }
```

- [ ] **Step 8: Run the targeted optimizer tests**

Run: `pytest tests\test_optimizer.py -v`

Expected: PASS for the new balanced-guarded tests and the existing win-rate-guarded coverage.

- [ ] **Step 9: Commit the scoring implementation**

```powershell
git add src\optimizer\grid_search.py tests\test_optimizer.py
git commit -m "feat: add balanced guarded optimizer scoring"
```

### Task 4: Verify regression safety and artifact shape

**Files:**
- Modify: `tests\test_optimizer.py` (only if minor assertion updates are needed)
- Reference: `src\optimizer\grid_search.py`, `src\config\settings.py`

- [ ] **Step 1: Ensure the existing artifact-writing test checks the new fields**

```python
    result_df = pd.read_csv(results_path)
    assert not result_df.empty
    assert {
        "roi",
        "max_drawdown",
        "total_bets_placed",
        "score",
        "worst_window_roi",
        "roi_std",
        "win_rate_std",
    }.issubset(result_df.columns)
```

- [ ] **Step 2: Run the focused regression suite**

Run: `pytest tests\test_optimizer.py tests\test_smoke_cli.py -v`

Expected: PASS with no CLI regressions in optimizer dry-run behavior.

- [ ] **Step 3: Run the broader baseline validation already present in the repo**

Run: `pytest -q`

Expected: PASS, or only pre-existing unrelated failures.

- [ ] **Step 4: Commit the regression-safe finish**

```powershell
git add tests\test_optimizer.py src\optimizer\grid_search.py src\config\settings.py
git commit -m "test: cover balanced optimizer artifacts"
```
