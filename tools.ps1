Set-StrictMode -Version Latest

function Get-FootballProjectRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return (Get-Location).Path
}

function Set-FootballPythonContext {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $env:PYTHONPATH = $ProjectRoot
}

function Test-FootballCliOption {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string]$Option
    )

    $helpOutput = & python -m src.main $Command --help 2>&1 | Out-String
    return $helpOutput -match [Regex]::Escape($Option)
}

function Invoke-FootballCli {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    Push-Location -Path $ProjectRoot
    try {
        & python -m src.main $Command @Arguments
    }
    finally {
        Pop-Location
    }
}

function Invoke-FootballBacktest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModelPath,

        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,

        [Parameter(Mandatory = $true)]
        [string]$RunName
    )

    $projectRoot = Get-FootballProjectRoot
    Set-FootballPythonContext -ProjectRoot $projectRoot

    $artifactsDir = Join-Path -Path $projectRoot -ChildPath "artifacts"
    $backtestBaseDir = Join-Path -Path $artifactsDir -ChildPath "backtest"
    $runOutputDir = Join-Path -Path $backtestBaseDir -ChildPath $RunName

    New-Item -ItemType Directory -Path $runOutputDir -Force | Out-Null

    $args = @("--force")

    if (Test-FootballCliOption -Command "backtest" -Option "--output-dir") {
        $args += @("--output-dir", $backtestBaseDir)
    }

    if (Test-FootballCliOption -Command "backtest" -Option "--run-id") {
        $args += @("--run-id", $RunName)
    }
    elseif (Test-FootballCliOption -Command "backtest" -Option "--run-name") {
        $args += @("--run-name", $RunName)
    }

    if (Test-FootballCliOption -Command "backtest" -Option "--input-path") {
        $args += @("--input-path", $ConfigPath)
    }
    elseif (Test-FootballCliOption -Command "backtest" -Option "--config") {
        $args += @("--config", $ConfigPath)
    }

    if (Test-FootballCliOption -Command "backtest" -Option "--model-path") {
        $args += @("--model-path", $ModelPath)
    }

    Invoke-FootballCli -Command "backtest" -Arguments $args -ProjectRoot $projectRoot
}

function Invoke-FootballOptimize {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModelPath,

        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,

        [Parameter(Mandatory = $true)]
        [string]$RunName
    )

    $projectRoot = Get-FootballProjectRoot
    Set-FootballPythonContext -ProjectRoot $projectRoot

    $artifactsDir = Join-Path -Path $projectRoot -ChildPath "artifacts"
    $optimizerBaseDir = Join-Path -Path $artifactsDir -ChildPath "optimizer"
    $runOutputDir = Join-Path -Path $optimizerBaseDir -ChildPath $RunName

    New-Item -ItemType Directory -Path $runOutputDir -Force | Out-Null

    $args = @("--force")

    if (Test-FootballCliOption -Command "optimize" -Option "--output-dir") {
        $args += @("--output-dir", $optimizerBaseDir)
    }

    if (Test-FootballCliOption -Command "optimize" -Option "--run-id") {
        $args += @("--run-id", $RunName)
    }
    elseif (Test-FootballCliOption -Command "optimize" -Option "--run-name") {
        $args += @("--run-name", $RunName)
    }

    if (Test-FootballCliOption -Command "optimize" -Option "--input-path") {
        $args += @("--input-path", $ConfigPath)
    }
    elseif (Test-FootballCliOption -Command "optimize" -Option "--config") {
        $args += @("--config", $ConfigPath)
    }

    if (Test-FootballCliOption -Command "optimize" -Option "--model-path") {
        $args += @("--model-path", $ModelPath)
    }

    Invoke-FootballCli -Command "optimize" -Arguments $args -ProjectRoot $projectRoot
}

function Invoke-FootballLiveDryRun {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModelPath,

        [int]$MaxAlerts = 3
    )

    $projectRoot = Get-FootballProjectRoot
    Set-FootballPythonContext -ProjectRoot $projectRoot

    $env:TELEGRAM_DRY_RUN = "true"

    $args = @(
        "--provider", "hkjc",
        "--model-path", $ModelPath,
        "--dry-run",
        "--edge-threshold", "0.0",
        "--confidence-threshold", "0.0",
        "--max-alerts", $MaxAlerts.ToString(),
        "--force"
    )

    Invoke-FootballCli -Command "live-run-once" -Arguments $args -ProjectRoot $projectRoot
}

function Invoke-FootballLive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModelPath,

        [int]$MaxAlerts = 1
    )

    $projectRoot = Get-FootballProjectRoot
    Set-FootballPythonContext -ProjectRoot $projectRoot

    $requiredEnvVars = @("TELEGRAM_DRY_RUN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    foreach ($varName in $requiredEnvVars) {
        if ([string]::IsNullOrWhiteSpace((Get-Item -Path "Env:$varName" -ErrorAction SilentlyContinue).Value)) {
            throw "Missing required environment variable: $varName"
        }
    }

    $args = @(
        "--provider", "hkjc",
        "--model-path", $ModelPath,
        "--live",
        "--edge-threshold", "0.0",
        "--confidence-threshold", "0.0",
        "--max-alerts", $MaxAlerts.ToString(),
        "--force"
    )

    Invoke-FootballCli -Command "live-run-once" -Arguments $args -ProjectRoot $projectRoot
}

# Quick start examples:
# Invoke-FootballBacktest -ModelPath "artifacts\model_bundle.pkl" -ConfigPath "configs\backtest\default.yml" -RunName "dev_h2h_recent"
# Invoke-FootballOptimize -ModelPath "artifacts\model_bundle.pkl" -ConfigPath "configs\optimize\default.yml" -RunName "dev_opt_grid"
# Invoke-FootballLiveDryRun -ModelPath "artifacts\model_bundle.pkl" -MaxAlerts 3
# Invoke-FootballLive -ModelPath "artifacts\model_bundle.pkl" -MaxAlerts 1
