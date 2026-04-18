# Onchain Monitor

Codex-first hotspot monitoring for onchain and exchange-adjacent signals.

This repository combines:

- Binance Web3 trend sources:
  - Social Hype
  - Trending
  - Top Search
  - Topic Rush
  - Smart Money Inflow
- OKX onchain sources:
  - aggregated signal alerts
  - hot token snapshots

The runtime is split into two paths:

- Burst alerts: deterministic polling + scoring + Discord webhook push
- Daily reports: Codex CLI generates morning / noon / evening summaries from stored state

## Repo Layout

- `plans/hotspot-monitor.md`
  - product and trigger strategy
- `monitor/`
  - runnable monitor implementation
- `.agents/skills/`
  - project-scoped skills used by Codex
- `skills-lock.json`
  - skill inventory snapshot for the project

## Quick Start

1. Review `monitor/config.example.yaml`
2. Copy it to `monitor/config.yaml`
3. Fill in your Discord webhooks
4. Install Python dependencies from `monitor/requirements.txt`
5. Make sure `onchainos` and `codex` are available in `PATH`
6. Run:

```powershell
python monitor\scripts\burst_scan.py --config monitor\config.yaml --dry-run
python monitor\scripts\send_report.py --kind morning --config monitor\config.yaml --dry-run
```

## Windows Scheduling

The supported Windows setup is:

- Task Scheduler for recurring burst and report jobs
- Startup folder launcher for one immediate burst run after user logon

Register everything with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File monitor\scripts\register_windows_tasks.ps1
```

## Privacy

The following local files are intentionally ignored:

- `monitor/config.yaml`
- `monitor/state/`
- `monitor/logs/`
- `.claude/`
- `.idea/`

That keeps webhooks, runtime state, and local editor settings out of git.
