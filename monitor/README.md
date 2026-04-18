# Monitor Runtime

This directory contains the runnable implementation of the hotspot monitor.

## What It Does

- Polls 7 signal sources on a fixed interval
- Normalizes events into a single asset model
- Applies hard filters, scoring, cooldowns, and quiet hours
- Pushes burst alerts to Discord
- Builds morning / noon / evening reports with local Codex CLI

## Current Data Sources

- Binance Social Hype
- Binance Trending
- Binance Top Search
- Binance Topic Rush
- Binance Smart Money Inflow
- OKX aggregated signal list via `onchainos`
- OKX hot tokens via `onchainos`

## Config

Copy:

```powershell
Copy-Item monitor\config.example.yaml monitor\config.yaml
```

Then set:

- `discord.webhook_reports`
- `discord.webhook_bursts`
- `report_provider`

`report_provider` options:

- `codex`
  - use local `codex exec`
- `disabled`
  - skip daily reports, keep burst alerts only

## Key Scripts

- `scripts/collect_snapshot.py`
  - collects and normalizes source data
- `scripts/burst_scan.py`
  - scoring and burst push flow
- `scripts/send_report.py`
  - daily report generation and push
- `scripts/run_burst_scan.ps1`
  - Windows wrapper with working directory and log file handling
- `scripts/run_send_report.ps1`
  - Windows wrapper for daily reports
- `scripts/register_windows_tasks.ps1`
  - registers Windows scheduled tasks and startup launcher

## Windows Scheduling

Recommended setup:

- `OnchainMonitor_Burst`
  - every 3 minutes
- `OnchainMonitor_MorningReport`
  - daily at 08:00
- `OnchainMonitor_NoonReport`
  - daily at 12:30
- `OnchainMonitor_EveningReport`
  - daily at 20:00
- Startup launcher:
  - `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OnchainMonitor_Burst.cmd`
  - runs one burst scan after user logon

Register or refresh these tasks with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File monitor\scripts\register_windows_tasks.ps1
```

## Logs

Wrapper scripts write daily log files into:

- `monitor/logs/burst_scan-YYYY-MM-DD.log`
- `monitor/logs/report-morning-YYYY-MM-DD.log`
- `monitor/logs/report-noon-YYYY-MM-DD.log`
- `monitor/logs/report-evening-YYYY-MM-DD.log`

## Smoke Tests

Burst dry run:

```powershell
python monitor\scripts\burst_scan.py --config monitor\config.yaml --dry-run
```

Morning report dry run:

```powershell
python monitor\scripts\send_report.py --kind morning --config monitor\config.yaml --dry-run
```

## Notes

- Runtime state is stored in `monitor/state/pushes.sqlite`
- `monitor/config.yaml`, `monitor/state/`, and `monitor/logs/` should stay out of git
- The runtime does not depend on Anthropic or OpenAI API keys for daily reports; it uses the local Codex CLI login
