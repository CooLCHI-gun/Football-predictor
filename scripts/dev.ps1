param(
    [ValidateSet("test", "backtest", "optimize-small", "alert-dryrun")]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    throw "Virtual environment not found at .venv. Create it first."
}

. .\.venv\Scripts\Activate.ps1

switch ($Task) {
    "test" {
        pytest -q
    }
    "backtest" {
        python -m src.main backtest --input-path data\processed\features_phase3_full.csv --force
    }
    "optimize-small" {
        python -m src.main optimize --input-path data\processed\features_phase3_full.csv --edge-grid "0.03" --confidence-grid "0.55" --policy-grid "fractional_kelly,vol_target" --kelly-grid "0.25" --max-stake-grid "0.02" --daily-exposure-grid "0.03" --force
    }
    "alert-dryrun" {
        python -m src.main alert --predictions-path artifacts\predictions_full.csv --edge-threshold 0.02 --confidence-threshold 0.55 --max-alerts 3
    }
}
