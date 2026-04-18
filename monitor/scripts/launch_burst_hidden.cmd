@echo off
wscript.exe "%~dp0launch_hidden.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%~dp0run_burst_scan.ps1"""
