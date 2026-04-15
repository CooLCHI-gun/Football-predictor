# Football predictor balanced ROI/win-rate optimizer design

## Problem

The current project already has a multi-objective optimizer that ranks strategy parameter sets with ROI, win rate, drawdown, risk-of-ruin, CLV, and placed-bet coverage. The next improvement should target a more balanced outcome: lift ROI and win rate together while keeping drawdown near 10-12% and preserving roughly 100-140 bets over the backtest horizon.

The main gap is that the optimizer currently relies on aggregate metrics and does not strongly prefer parameter sets that are stable across outer rolling windows. This can allow average results to look acceptable even when one or two windows carry most of the performance.

## Proposed approach

Add a new guarded balanced optimizer mode that:

1. Applies explicit eligibility gates before ranking.
2. Extends outer-window evaluation with stability metrics.
3. Scores remaining parameter sets with a balanced objective that rewards ROI and win rate while penalizing drawdown, risk, low coverage, and unstable window-to-window behavior.

This keeps the existing backtest CLI and optimizer workflow intact while making the optimizer choose parameter sets that better match the desired production profile.

## Why this approach

### Option A: Tighten thresholds only

Raise edge and confidence thresholds and reduce alerts without changing scoring.

- Pros: minimal code changes.
- Cons: often increases win rate by collapsing bet count too far, making it hard to stay near the desired 100-140 bet range.

### Option B: Guarded balanced scoring with stability penalty (recommended)

Keep the existing grid search and walk-forward structure, but improve the objective function and outer-window summary.

- Pros: fits the existing architecture, keeps change scope small, and directly optimizes the target trade-off.
- Cons: adds a few more configuration knobs and scoring fields.

### Option C: Change model/features

Modify the feature pipeline or retraining logic.

- Pros: potentially higher ceiling.
- Cons: larger scope, more uncertainty, and slower feedback than improving strategy selection first.

## Design

### 1. Scoring architecture

Add a new optimizer scoring branch, tentatively `BALANCED_GUARDED`, with two phases:

1. **Eligibility gates**
   - Reject or heavily penalize parameter sets when:
     - `max_drawdown > 0.12`
     - `min_window_bets < 100`
     - `roi <= 0`
2. **Balanced ranking**
   - Rank eligible parameter sets with a weighted score that:
     - rewards ROI
     - rewards win rate
     - rewards adequate placed-bet coverage
     - penalizes drawdown
     - penalizes risk-of-ruin
     - penalizes unstable outer-window performance

This preserves the current pattern where invalid combinations can still fall back to a best available result if every run violates a guard, but normal best-selection will prefer parameter sets aligned to the balanced target.

### 2. Outer-window stability metrics

Extend `_evaluate_param_set_with_outer_rolling()` so the aggregated summary includes both averages and stability signals derived from per-window summaries.

Add the following outputs:

- `roi_std`
- `win_rate_std`
- `worst_window_roi`
- `worst_window_win_rate`

These values are lightweight to compute because the function already collects each window summary in memory.

Stability will be modeled as a penalty in the scoring function so that two strategies with similar averages will rank differently when one is materially less consistent across windows.

### 3. Components to change

#### `src\optimizer\grid_search.py`

- Extend `ObjectiveWeights` with balanced-guarded settings such as:
  - drawdown cap
  - minimum window bet threshold
  - minimum ROI threshold
  - stability penalty weights
- Update `_evaluate_param_set_with_outer_rolling()` to compute the new window-dispersion metrics.
- Update `compute_objective_score()` to route to a new balanced-guarded scoring function.
- Persist the new stability metrics into `params_results.csv` and `best_params.json`.
- If all runs violate guards, keep the existing fallback behavior but annotate the best payload to show it is a fallback winner.

#### `src\config\settings.py`

- Add environment-backed settings for the new balanced-guarded thresholds and penalty weights.
- Follow the current settings normalization pattern so mode and numeric values remain easy to override from CLI workflows and GitHub Actions.

#### `tests\test_optimizer.py`

- Add tests for eligibility gates:
  - drawdown above cap
  - negative or zero ROI
  - insufficient minimum window bets
- Add tests for stability-aware ranking:
  - similar average ROI and win rate, but lower dispersion should rank higher
- Add assertions that the new summary/artifact fields are written.

## Data flow

1. `optimize_strategy()` generates parameter combinations as it does today.
2. For each parameter set, `_evaluate_param_set_with_outer_rolling()` runs one or more outer windows and returns aggregate metrics plus new stability metrics.
3. `compute_objective_score()` applies guarded-balanced ranking.
4. The optimizer writes the extended per-run results to `params_results.csv`.
5. The best result is written to `best_params.json`, including a note when chosen through fallback because no run satisfied all gates.

## Error handling

- If outer rolling is disabled or insufficient data exists, preserve the current single-summary fallback path.
- When stability metrics cannot be computed from multiple windows, write safe defaults consistent with existing summary behavior.
- Do not silently ignore invalid modes; continue using the existing settings normalization pattern.

## Testing strategy

Focus on deterministic optimizer tests that mock backtest summaries rather than expensive end-to-end retraining.

Key coverage:

1. Guard logic rejects unstable or out-of-profile candidates.
2. Stable candidates beat volatile candidates when averages are otherwise similar.
3. Output artifacts include the new stability fields.
4. Existing fallback behavior remains intact when every run fails a guard.

## Success criteria

On the same historical dataset and walk-forward setup, the new balanced-guarded mode should bias optimizer selection toward parameter sets that:

- keep drawdown around 10-12% or lower,
- maintain at least about 100 minimum window bets,
- avoid negative-ROI parameter sets,
- and show less window-to-window fragility than the current aggregate-only ranking.

## Out of scope

- Changing the core prediction model or feature engineering.
- Reworking the CLI surface beyond exposing new settings through the existing configuration pattern.
- Introducing new backtest frameworks or replacing the current optimizer structure.
