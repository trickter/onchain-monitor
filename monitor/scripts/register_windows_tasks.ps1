$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wscriptExe = (Get-Command wscript.exe).Source
$vbsLauncher = Join-Path $scriptDir "launch_hidden.vbs"
$burstCmd = Join-Path $scriptDir "launch_burst_hidden.cmd"
$reportCmd = Join-Path $scriptDir "launch_report_hidden.cmd"
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
        [string]$CommandPath
    )

    $content = @(
        "Set shell = CreateObject(""WScript.Shell"")",
        "shell.Run Chr(34) & ""$CommandPath"" & Chr(34), 0, False"
    )
    Set-Content -LiteralPath $LauncherPath -Value $content -Encoding ASCII
}

$burstRun = "`"$wscriptExe`" `"$vbsLauncher`" `"`"$burstCmd`"`""
$morningRun = "`"$wscriptExe`" `"$vbsLauncher`" `"`"$reportCmd`" morning`""
$noonRun = "`"$wscriptExe`" `"$vbsLauncher`" `"`"$reportCmd`" noon`""
$eveningRun = "`"$wscriptExe`" `"$vbsLauncher`" `"`"$reportCmd`" evening`""

Register-OnchainTask -TaskName "OnchainMonitor_Burst" -Schedule "MINUTE" -Modifier "3" -TaskRun $burstRun
Register-OnchainTask -TaskName "OnchainMonitor_MorningReport" -Schedule "DAILY" -StartTime "08:00" -TaskRun $morningRun
Register-OnchainTask -TaskName "OnchainMonitor_NoonReport" -Schedule "DAILY" -StartTime "12:30" -TaskRun $noonRun
Register-OnchainTask -TaskName "OnchainMonitor_EveningReport" -Schedule "DAILY" -StartTime "20:00" -TaskRun $eveningRun

$startupLauncher = Join-Path $startupDir "OnchainMonitor_Burst.vbs"
Install-StartupLauncher -LauncherPath $startupLauncher -CommandPath $burstCmd

Write-Host "Scheduled tasks registered successfully."
