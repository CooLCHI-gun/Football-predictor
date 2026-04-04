param(
    [string]$Provider = "mock",
    [string]$ModelPath = "artifacts\model_bundle.pkl",
    [double]$EdgeThreshold = 0.03,
    [double]$ConfidenceThreshold = 0.55,
    [string]$Policy = "fractional_kelly",
    [int]$MaxAlerts = 3,
    [string]$OutputDir = "artifacts\live",
    [int]$LoopIntervalSeconds = 5,
    [int]$LoopMaxCycles = 2,
    [string]$RunId = "20260403_pm1",
    [switch]$Live,
    [switch]$ActivateVenv
)

$ErrorActionPreference = "Stop"

if ($ActivateVenv) {
    if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
        throw "Virtual environment not found at .venv."
    }
    . .\.venv\Scripts\Activate.ps1
}

$modeArg = if ($Live) { "--live" } else { "--dry-run" }

$results = [System.Collections.Generic.List[object]]::new()

function Add-Result {
    param(
        [string]$Step,
        [string]$Check,
        [bool]$Pass,
        [string]$Detail
    )

    $results.Add([PSCustomObject]@{
        Step   = $Step
        Check  = $Check
        Pass   = if ($Pass) { "PASS" } else { "FAIL" }
        Detail = $Detail
    })
}

function Invoke-Capture {
    param([string]$Command)

    $output = & cmd.exe /d /c "$Command 2>&1" | Out-String
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }

    return [PSCustomObject]@{
        ExitCode = [int]$exitCode
        Output   = $output
    }
}

function Assert-Command {
    param(
        [string]$Step,
        [string]$Name,
        [string]$Command,
        [string[]]$MustContain
    )

    $res = Invoke-Capture -Command $Command
    $ok = $res.ExitCode -eq 0

    if ($ok -and $MustContain -and $MustContain.Count -gt 0) {
        foreach ($token in $MustContain) {
            if ($res.Output -notmatch [regex]::Escape($token)) {
                $ok = $false
                break
            }
        }
    }

    $detail = "exit=$($res.ExitCode)"
    if (-not $ok) {
        $detail = "$detail | output: $($res.Output.Trim())"
    }

    Add-Result -Step $Step -Check $Name -Pass $ok -Detail $detail
    return $res
}

Write-Host "[Phase6 Acceptance] Step 1: CLI help"
Assert-Command -Step "1" -Name "main help has live commands" -Command "python -m src.main --help" -MustContain @("live-run-once", "live-loop") | Out-Null
Assert-Command -Step "1" -Name "live-run-once help" -Command "python -m src.main live-run-once --help" -MustContain @("--provider", "--model-path", "--dry-run", "--run-id") | Out-Null
Assert-Command -Step "1" -Name "live-loop help" -Command "python -m src.main live-loop --help" -MustContain @("--interval-seconds", "--max-cycles", "--dry-run") | Out-Null

Write-Host "[Phase6 Acceptance] Step 2: live-run-once"
$onceCmd = @(
    "python -m src.main live-run-once",
    "--provider $Provider",
    "--model-path $ModelPath",
    "--edge-threshold $EdgeThreshold",
    "--confidence-threshold $ConfidenceThreshold",
    "--policy $Policy",
    "--max-alerts $MaxAlerts",
    "$modeArg",
    "--output-dir $OutputDir",
    "--force"
) -join " "
$onceRes = Assert-Command -Step "2" -Name "live-run-once command" -Command $onceCmd -MustContain @("Phase 6 live-run-once completed.")

$rootRequired = @(
    "live_snapshot.csv",
    "live_candidates.csv",
    "live_alert_log.csv",
    "live_status.json",
    "dashboard.html"
)
foreach ($name in $rootRequired) {
    $path = Join-Path $OutputDir $name
    Add-Result -Step "2" -Check "artifact exists: $name" -Pass (Test-Path $path) -Detail $path
}

$rootAlertLog = Join-Path $OutputDir "live_alert_log.csv"
$alertLineCountBeforeLoop = if (Test-Path $rootAlertLog) { (Get-Content $rootAlertLog).Count } else { 0 }

Write-Host "[Phase6 Acceptance] Step 3: live-loop"
$loopCmd = @(
    "python -m src.main live-loop",
    "--provider $Provider",
    "--model-path $ModelPath",
    "--interval-seconds $LoopIntervalSeconds",
    "--max-cycles $LoopMaxCycles",
    "--edge-threshold $EdgeThreshold",
    "--confidence-threshold $ConfidenceThreshold",
    "--policy $Policy",
    "--max-alerts $MaxAlerts",
    "$modeArg",
    "--output-dir $OutputDir",
    "--force"
) -join " "
$loopRes = Assert-Command -Step "3" -Name "live-loop command" -Command $loopCmd -MustContain @("Phase 6 live-loop completed: cycles=$LoopMaxCycles")

$cycleBoundaryOk = ($loopRes.Output -match "cycle started") -and ($loopRes.Output -match "cycle completed")
Add-Result -Step "3" -Check "cycle boundaries in log" -Pass $cycleBoundaryOk -Detail "look for 'cycle started' and 'cycle completed'"

$statusPath = Join-Path $OutputDir "live_status.json"
$statusText = if (Test-Path $statusPath) { Get-Content $statusPath -Raw } else { "" }
$statusUpdated = $statusText -match "last_success_time_utc"
Add-Result -Step "3" -Check "live_status updated" -Pass $statusUpdated -Detail $statusPath

$alertLineCountAfterLoop = if (Test-Path $rootAlertLog) { (Get-Content $rootAlertLog).Count } else { 0 }
$alertAppended = $alertLineCountAfterLoop -gt $alertLineCountBeforeLoop
Add-Result -Step "3" -Check "live_alert_log appended" -Pass $alertAppended -Detail "before=$alertLineCountBeforeLoop after=$alertLineCountAfterLoop"

Write-Host "[Phase6 Acceptance] Step 4: run-id"
$rootStatusWriteTimeBefore = if (Test-Path $statusPath) { (Get-Item $statusPath).LastWriteTimeUtc } else { $null }

$runIdCmd = @(
    "python -m src.main live-run-once",
    "--provider $Provider",
    "--model-path $ModelPath",
    "--edge-threshold $EdgeThreshold",
    "--confidence-threshold $ConfidenceThreshold",
    "--policy $Policy",
    "--max-alerts $MaxAlerts",
    "$modeArg",
    "--output-dir $OutputDir",
    "--run-id $RunId",
    "--force"
) -join " "
Assert-Command -Step "4" -Name "live-run-once with run-id" -Command $runIdCmd -MustContain @("run_id=$RunId") | Out-Null

$runDir = Join-Path $OutputDir $RunId
foreach ($name in $rootRequired) {
    $path = Join-Path $runDir $name
    Add-Result -Step "4" -Check "run-id artifact exists: $name" -Pass (Test-Path $path) -Detail $path
}

$rootStatusWriteTimeAfter = if (Test-Path $statusPath) { (Get-Item $statusPath).LastWriteTimeUtc } else { $null }
$rootUnchanged = $true
if ($null -ne $rootStatusWriteTimeBefore -and $null -ne $rootStatusWriteTimeAfter) {
    $rootUnchanged = $rootStatusWriteTimeAfter -eq $rootStatusWriteTimeBefore
}
Add-Result -Step "4" -Check "root status unaffected by run-id" -Pass $rootUnchanged -Detail "before=$rootStatusWriteTimeBefore after=$rootStatusWriteTimeAfter"

Write-Host "[Phase6 Acceptance] Step 5: dashboard markers"
$dashboardPath = Join-Path $OutputDir "dashboard.html"
$dashboardText = if (Test-Path $dashboardPath) { Get-Content $dashboardPath -Raw } else { "" }
$markers = @(
    "System Status",
    "Mode Separation",
    "Cycle Counters",
    "Upcoming / Live Snapshot",
    "Alert Candidates",
    "Alert Log",
    "Event Log",
    "HKJC Football Phase 6 Live Monitor"
)

foreach ($marker in $markers) {
    Add-Result -Step "5" -Check "dashboard has marker: $marker" -Pass ($dashboardText -match [regex]::Escape($marker)) -Detail $dashboardPath
}

Write-Host ""
Write-Host "=== Phase 6 Acceptance Summary ==="
$results | Format-Table -AutoSize

$failed = @($results | Where-Object { $_.Pass -eq "FAIL" })
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "Overall: PASS ($($results.Count) checks)" -ForegroundColor Green
    exit 0
}

Write-Host "Overall: FAIL ($($failed.Count) failed / $($results.Count) checks)" -ForegroundColor Red
exit 1
