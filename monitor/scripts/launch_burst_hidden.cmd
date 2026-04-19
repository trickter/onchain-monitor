@echo off
set SCRIPT_DIR=%~dp0
wscript.exe "%SCRIPT_DIR%launch_hidden.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%SCRIPT_DIR%run_burst_scan.ps1"""
