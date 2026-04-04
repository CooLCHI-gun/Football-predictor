$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python venv not found at $pythonExe. Please create .venv (Python 3.11) first."
}

$pyVersion = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVersion.Trim() -ne "3.11") {
    throw "Expected Python 3.11 in venv, but got $pyVersion"
}

$backtestTime = if ($env:BACKTEST_TIME) { $env:BACKTEST_TIME } else { "01:30" }
$optimizeTime = if ($env:OPTIMIZE_TIME) { $env:OPTIMIZE_TIME } else { "03:30" }
$timezoneName = if ($env:TIMEZONE_NAME) { $env:TIMEZONE_NAME } else { "Asia/Hong_Kong" }
$featurePath = if ($env:FEATURE_PATH) { $env:FEATURE_PATH } else { "data/processed/features_phase3_full.csv" }
$backtestOutputDir = if ($env:BACKTEST_OUTPUT_DIR) { $env:BACKTEST_OUTPUT_DIR } else { "artifacts/backtest" }
$optimizerOutputDir = if ($env:OPTIMIZER_OUTPUT_DIR) { $env:OPTIMIZER_OUTPUT_DIR } else { "artifacts/optimizer" }
$optimizerMaxRuns = if ($env:OPTIMIZER_MAX_RUNS) { $env:OPTIMIZER_MAX_RUNS } else { "120" }
$liveIntervalSeconds = if ($env:LIVE_INTERVAL_SECONDS) { $env:LIVE_INTERVAL_SECONDS } else { "300" }
$liveProvider = if ($env:LIVE_PROVIDER) { $env:LIVE_PROVIDER } else { "hkjc" }
$liveModelPath = if ($env:LIVE_MODEL_PATH) { $env:LIVE_MODEL_PATH } else { "artifacts/model_bundle.pkl" }
$liveEdgeThreshold = if ($env:LIVE_EDGE_THRESHOLD) { $env:LIVE_EDGE_THRESHOLD } else { "0.02" }
$liveConfidenceThreshold = if ($env:LIVE_CONFIDENCE_THRESHOLD) { $env:LIVE_CONFIDENCE_THRESHOLD } else { "0.56" }
$liveMaxAlerts = if ($env:LIVE_MAX_ALERTS) { $env:LIVE_MAX_ALERTS } else { "3" }
$liveOutputDir = if ($env:LIVE_OUTPUT_DIR) { $env:LIVE_OUTPUT_DIR } else { "artifacts/live" }
$liveMode = if ($env:LIVE_MODE) { $env:LIVE_MODE.ToLowerInvariant() } else { "dry" }

$dailyArgs = @(
    "-m", "src.main",
    "daily-maintenance",
    "--timezone-name", $timezoneName,
    "--backtest-time", $backtestTime,
    "--optimize-time", $optimizeTime,
    "--backtest-input-path", $featurePath,
    "--optimize-input-path", $featurePath,
    "--backtest-output-dir", $backtestOutputDir,
    "--optimize-output-dir", $optimizerOutputDir,
    "--use-date-run-id",
    "--use-prediction-cache",
    "--max-runs", $optimizerMaxRuns,
    "--repeat-daily",
    "--force"
)

$liveArgs = @(
    "-m", "src.main",
    "live-loop",
    "--provider", $liveProvider,
    "--model-path", $liveModelPath,
    "--interval-seconds", $liveIntervalSeconds,
    "--edge-threshold", $liveEdgeThreshold,
    "--confidence-threshold", $liveConfidenceThreshold,
    "--max-alerts", $liveMaxAlerts,
    "--output-dir", $liveOutputDir,
    "--force"
)

if ($liveMode -eq "live") {
    $liveArgs += "--live"
} else {
    $liveArgs += "--dry-run"
}

Push-Location $projectRoot
try {
    $dailyProcess = Start-Process -FilePath $pythonExe -ArgumentList $dailyArgs -NoNewWindow -PassThru
    try {
        & $pythonExe @liveArgs
    }
    finally {
        if ($dailyProcess -and -not $dailyProcess.HasExited) {
            Stop-Process -Id $dailyProcess.Id -Force
        }
    }
}
finally {
    Pop-Location
}
