@echo off
wscript.exe "%~dp0launch_hidden.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%~dp0run_send_report.ps1"" -Kind %1"
