$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$powershellExe = (Get-Command powershell.exe).Source
$burstScript = Join-Path $scriptDir "run_burst_scan.ps1"
$reportScript = Join-Path $scriptDir "run_send_report.ps1"
$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"

function Register-OnchainTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [Parameter(Mandatory = $true)]
        [string]$Schedule,
        [Parameter(Mandatory = $true)]
        [string]$TaskRun,
        [string]$Modifier = "",
        [string]$StartTime = ""
    )

    $args = @("/Create", "/TN", $TaskName, "/SC", $Schedule, "/TR", $TaskRun, "/F")
    if ($Modifier) {
        $args += @("/MO", $Modifier)
    }
    if ($StartTime) {
        $args += @("/ST", $StartTime)
    }

    & schtasks.exe @args | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled task $TaskName"
    }
}

function Install-StartupLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LauncherPath,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath
    )

    $content = @(
        "@echo off",
        "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""
    )
    Set-Content -LiteralPath $LauncherPath -Value $content -Encoding ASCII
}

$burstRun = "`"$powershellExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$burstScript`""
$morningRun = "`"$powershellExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$reportScript`" -Kind morning"
$noonRun = "`"$powershellExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$reportScript`" -Kind noon"
$eveningRun = "`"$powershellExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$reportScript`" -Kind evening"

Register-OnchainTask -TaskName "OnchainMonitor_Burst" -Schedule "MINUTE" -Modifier "3" -TaskRun $burstRun
Register-OnchainTask -TaskName "OnchainMonitor_MorningReport" -Schedule "DAILY" -StartTime "08:00" -TaskRun $morningRun
Register-OnchainTask -TaskName "OnchainMonitor_NoonReport" -Schedule "DAILY" -StartTime "12:30" -TaskRun $noonRun
Register-OnchainTask -TaskName "OnchainMonitor_EveningReport" -Schedule "DAILY" -StartTime "20:00" -TaskRun $eveningRun

$startupLauncher = Join-Path $startupDir "OnchainMonitor_Burst.cmd"
Install-StartupLauncher -LauncherPath $startupLauncher -ScriptPath $burstScript

Write-Host "Scheduled tasks registered successfully."
