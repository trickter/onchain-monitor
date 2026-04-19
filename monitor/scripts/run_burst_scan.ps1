$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorDir = Split-Path -Parent $scriptDir
$projectDir = Split-Path -Parent $monitorDir
$configPath = Join-Path $monitorDir "config.yaml"
$burstScriptPath = Join-Path $scriptDir "burst_scan.py"
$logDir = Join-Path $monitorDir "logs"

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$timestamp = Get-Date -Format "yyyy-MM-dd"
$logPath = Join-Path $logDir "burst_scan-$timestamp.log"

Push-Location $projectDir
try {
    Add-Content -Path $logPath -Value ("[{0}] burst scan start" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    $command = 'python "{0}" --config "{1}" >> "{2}" 2>&1' -f $burstScriptPath, $configPath, $logPath
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = "cmd.exe"
    $startInfo.Arguments = "/d /c $command"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WorkingDirectory = $projectDir

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $null = $process.Start()

    if (-not $process.WaitForExit(900000)) {
        try {
            $process.Kill()
        }
        catch {
        }
        Add-Content -Path $logPath -Value ("[{0}] burst scan timeout after 900s" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
        throw "burst_scan.py timed out after 900 seconds"
    }

    if ($process.ExitCode -ne 0) {
        throw "burst_scan.py exited with code $($process.ExitCode)"
    }
    Add-Content -Path $logPath -Value ("[{0}] burst scan success" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
}
finally {
    Pop-Location
}
