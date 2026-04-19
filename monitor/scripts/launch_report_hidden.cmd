@echo off
set SCRIPT_DIR=%~dp0
wscript.exe "%SCRIPT_DIR%launch_hidden.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%SCRIPT_DIR%run_send_report.ps1"" -Kind %1"
