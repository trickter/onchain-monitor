$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorDir = Split-Path -Parent $scriptDir
$projectDir = Split-Path -Parent $monitorDir
$configPath = Join-Path $monitorDir "config.yaml"
$logDir = Join-Path $monitorDir "logs"

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$timestamp = Get-Date -Format "yyyy-MM-dd"
$logPath = Join-Path $logDir "burst_scan-$timestamp.log"

Push-Location $projectDir
try {
    Add-Content -Path $logPath -Value ("[{0}] burst scan start" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    $command = 'python "monitor\scripts\burst_scan.py" --config "{0}" >> "{1}" 2>&1' -f $configPath, $logPath
    & cmd.exe /d /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "burst_scan.py exited with code $LASTEXITCODE"
    }
    Add-Content -Path $logPath -Value ("[{0}] burst scan success" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
}
finally {
    Pop-Location
}
