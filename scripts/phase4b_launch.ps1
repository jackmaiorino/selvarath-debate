[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Authorization,
    [ValidateSet('preflight','run','status')][string]$Mode = 'run',
    [switch]$Payload,
    [string]$LogDirectory
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$authorizationPath = (Resolve-Path -LiteralPath $Authorization).Path
$config = Get-Content -LiteralPath $authorizationPath -Raw | ConvertFrom-Json
if ($Mode -ne 'status' -and ($config.paid_execution_authorized -ne $true -or $config.stage -ne '4B')) { throw 'A bounded Phase 4B adjudication authorization is required.' }
$inputPath = (Resolve-Path -LiteralPath $config.inputs).Path
$runDirectory = [IO.Path]::GetFullPath($config.run_directory)
$pythonPath = (Resolve-Path -LiteralPath (Join-Path $repo '.venv\Scripts\python.exe')).Path

function Write-NewJson([string]$Path, $Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 8))
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Quote-Arguments([string[]]$Items) {
    foreach ($item in $Items) {
        if ($item.Contains('"') -or $item.Contains("`n") -or $item.EndsWith('\')) { throw 'Unsupported command argument.' }
    }
    return '"' + ($Items -join '" "') + '"'
}

if (-not $Payload) {
    $launchId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    $logs = Join-Path $runDirectory ('launches\' + $launchId)
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $shellPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launchArgs = Quote-Arguments @('-NoProfile','-File',$PSCommandPath,'-Authorization',$authorizationPath,'-Mode',$Mode,'-Payload','-LogDirectory',$logs)
    # Transfer credentials only through the local process environment, never argv or files.
    $properties = @{
        ShowWindow=[uint16]0
        CreateFlags=[uint32]0x09000400
        EnvironmentVariables=[string[]]@(Get-ChildItem Env: | ForEach-Object { $_.Name + '=' + $_.Value })
    }
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property $properties
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine=('"' + $shellPath + '" ' + $launchArgs)
        CurrentDirectory=$repo
        ProcessStartupInformation=$startup
    }
    if ($created.ReturnValue -ne 0) { throw ('Detached creation failed: ' + $created.ReturnValue) }
    $receipt = [PSCustomObject]@{wrapper_pid=$created.ProcessId;launched_utc=[DateTime]::UtcNow.ToString('o');mode=$Mode;log_directory=$logs;run_directory=$runDirectory;hidden=$true;broker='Win32_Process.Create'}
    Write-NewJson (Join-Path $logs 'launch.json') $receipt
    $receipt | ConvertTo-Json -Depth 4
    exit 0
}

$logs = (Resolve-Path -LiteralPath $LogDirectory).Path
$runnerArgs = Quote-Arguments @('-B','-m','rejudge.phase4b_runner','--authorization',$authorizationPath,'--inputs',$inputPath,'--run-dir',$runDirectory,'--mode',$Mode)
$outFile=$null; $errFile=$null; $process=$null; $runnerPid=$null; $exitCode=1; $failure=$null
try {
    $outFile = [IO.File]::Open((Join-Path $logs 'stdout.log'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $errFile = [IO.File]::Open((Join-Path $logs 'stderr.log'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$pythonPath; $info.Arguments=$runnerArgs; $info.WorkingDirectory=$repo
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$info
    if (-not $process.Start()) { throw 'Runner process failed to start.' }
    $runnerPid=$process.Id
    Write-NewJson (Join-Path $logs 'runner.json') ([PSCustomObject]@{wrapper_pid=$PID;runner_pid=$runnerPid;started_utc=[DateTime]::UtcNow.ToString('o');mode=$Mode;python_path=$pythonPath})
    $copyOut=$process.StandardOutput.BaseStream.CopyToAsync($outFile)
    $copyErr=$process.StandardError.BaseStream.CopyToAsync($errFile)
    $process.WaitForExit()
    $copyOut.GetAwaiter().GetResult(); $copyErr.GetAwaiter().GetResult()
    $outFile.Flush($true); $errFile.Flush($true)
    $exitCode=$process.ExitCode
} catch {
    $failure=$_.Exception.GetType().FullName
} finally {
    if ($outFile) { $outFile.Dispose() }
    if ($errFile) { $errFile.Dispose() }
    if ($process) { $process.Dispose() }
    Write-NewJson (Join-Path $logs 'exit.json') ([PSCustomObject]@{wrapper_pid=$PID;runner_pid=$runnerPid;ended_utc=[DateTime]::UtcNow.ToString('o');exit_code=$exitCode;wrapper_failure_type=$failure;mode=$Mode})
}
exit $exitCode
