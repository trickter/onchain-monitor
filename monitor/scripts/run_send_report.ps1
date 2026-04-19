param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "noon", "evening")]
    [string]$Kind
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorDir = Split-Path -Parent $scriptDir
$projectDir = Split-Path -Parent $monitorDir
$configPath = Join-Path $monitorDir "config.yaml"
$reportScriptPath = Join-Path $scriptDir "send_report.py"
$logDir = Join-Path $monitorDir "logs"

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$timestamp = Get-Date -Format "yyyy-MM-dd"
$logPath = Join-Path $logDir "report-$Kind-$timestamp.log"

Push-Location $projectDir
try {
    Add-Content -Path $logPath -Value ("[{0}] {1} report start" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Kind)
    $command = 'python "{0}" --kind {1} --config "{2}" >> "{3}" 2>&1' -f $reportScriptPath, $Kind, $configPath, $logPath
    & cmd.exe /d /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "send_report.py exited with code $LASTEXITCODE"
    }
    Add-Content -Path $logPath -Value ("[{0}] {1} report success" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Kind)
}
finally {
    Pop-Location
}
