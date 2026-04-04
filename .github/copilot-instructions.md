# Copilot Instructions for HKJC Football Project

## Scope and Architecture
- This repository is for HKJC-oriented football full-time handicap research and execution.
- Settlement and labeling scope is full-time only: 90 minutes plus injury time.
- Never include extra time or penalty shootouts in settlement, labeling, backtests, or alerts.
- Keep production logic under src/.
- Notebooks are exploration-only and must not contain production business logic.

## Phase Governance
- Work phase-by-phase and keep each phase verifiable before moving to the next.
- Do not implement future-phase logic unless explicitly requested.
- MVP validation must include at least one real-data historical backtest with 100+ matches.
- Treat 100+ matches as an MVP minimum only, not robustness proof.
- Robust evidence usually requires larger samples with rolling and/or walk-forward evaluation.

## Data Source Policy
- Primary orientation is HKJC market structure.
- Non-HKJC real historical handicap datasets are allowed in Phase 1-3 when HKJC historical export is unavailable.
- All documentation and reports must explicitly label non-HKJC backtests.
- Reports must warn that non-HKJC results may not match HKJC pricing/execution conditions.

## Engineering Standards
- Use full type hints on new functions and classes.
- Keep functions short and testable.
- Use config-driven thresholds and strategy rules.
- Avoid look-ahead bias and leakage in all data, features, and backtests.
- Prefer provider-based architecture so HKJC-specific import can be added without major refactoring.

## Reporting Standards
- Do not claim profitability from prediction accuracy alone.
- Backtest reporting must include win rate and ROI together with drawdown context.
- Metric definitions:
  - Win rate = winning bets / total settled bets
  - ROI = (total return - total stake) / total stake

## Documentation Standards
- README operational manual must remain in Traditional Chinese.
- README must be practical and copy-paste friendly for VS Code local usage.
- If assumptions are uncertain, mark TODO and document assumptions clearly.

## Verification Standards
- Run verification commands after each implementation phase.
- Report command outputs honestly, including failures or partial completion.
- Do not fabricate backtest or test outcomes.
