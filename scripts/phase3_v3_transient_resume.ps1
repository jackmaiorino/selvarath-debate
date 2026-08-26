# Bounded transient auto-resume for the phase3-v3 successor canary driver.
# Mirrors the phase-2 auto-resume policy (rejudge/phase2_canary_auto_resume_*.json):
# relaunch ONLY when the driver halts on UnknownChargeHalt whose latest error-log line
# matches an enumerated benign transient signature. Every relaunch re-runs the driver's
# own full validation, aggregate cap, and uncertain-ceiling enforcement. Anything else
# (checker_malformed, ceiling, no-progress, validation refusals) stops this wrapper for
# orchestrator review. Grants no authority and changes no cap.
param(
    [string]$Manifest = "rejudge/phase3_v3_run_manifest_preflight_r16_2026-08-26.json",
    [string]$Authorization = "rejudge/phase3_v3_canary_authorization_r8_2026-08-26.json",
    [string]$ErrorLog = "E:/selvarath-archive/phase3-v3r7-raised-ceiling-2026-08-26/phase3_v3_error_log.jsonl",
    [int]$MaxRelaunches = 12,
    [int]$BackoffSeconds = 60
)
$transient = "(Request timed out\.|Connection error\.|forcibly closed|Error code: 429|Error code: 500|Error code: 502|Error code: 503|streaming response ended without usage chunk)"
$relaunches = 0
while ($true) {
    $stderrFile = Join-Path $env:TEMP ("phase3_v3_drive_stderr_" + $relaunches + ".txt")
    & .\.venv\Scripts\python.exe -m rejudge.phase3_v3_live --manifest $Manifest --authorization $Authorization --drive-formal 2>$stderrFile
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "DRIVE CONVERGED AND FINALIZED (exit 0) after $relaunches relaunches"
        exit 0
    }
    $stderrText = ""
    if (Test-Path $stderrFile) { $stderrText = Get-Content $stderrFile -Raw }
    $lastError = ""
    if (Test-Path $ErrorLog) { $lastError = (Get-Content $ErrorLog -Tail 1) }
    $isUnknownChargeHalt = $stderrText -match "UnknownChargeHalt"
    $isTransient = $lastError -match $transient
    if (-not ($isUnknownChargeHalt -and $isTransient)) {
        Write-Host "NON-TRANSIENT HALT (exit $code): orchestrator review required"
        Write-Host ("stderr: " + $stderrText.Trim())
        Write-Host ("last error-log line: " + $lastError)
        exit 2
    }
    $relaunches += 1
    if ($relaunches -gt $MaxRelaunches) {
        Write-Host "RELAUNCH BOUND ($MaxRelaunches) EXHAUSTED: orchestrator review required"
        exit 3
    }
    Write-Host ("transient UnknownChargeHalt #" + $relaunches + "; relaunching after " + $BackoffSeconds + "s: " + $lastError)
    Start-Sleep -Seconds $BackoffSeconds
}
