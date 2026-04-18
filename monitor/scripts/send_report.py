"""
Daily report generator.

Reads recent events from SQLite, calls local Codex CLI with a report
prompt, then posts the generated summary to Discord #reports webhook.

Usage:
    python send_report.py --kind morning
    python send_report.py --kind noon
    python send_report.py --kind evening
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from monitor.lib import config as cfg_mod
from monitor.lib import notifier, state

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# hours of history to pull for each report type
_WINDOW_HOURS = {"morning": 24, "noon": 8, "evening": 8}


def _load_prompt(kind: str) -> str:
    path = _PROMPTS_DIR / f"report_{kind}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _build_context(kind: str) -> str:
    hours = _WINDOW_HOURS.get(kind, 24)
    recent = state.recent_assets(hours=hours)
    push_log = state.recent_push_log(hours=hours)

    # summarize top assets
    top_assets = [a for a in recent if (a.get("last_score") or 0) >= 4][:20]
    bursts = [p for p in push_log if p.get("tier") in ("hot", "critical")]

    ctx: dict = {
        "report_kind": kind,
        "window_hours": hours,
        "top_scored_assets": [
            {
                "symbol": a.get("symbol"),
                "chain": a.get("chain"),
                "contract": a.get("contract"),
                "score": a.get("last_score"),
                "tier": a.get("last_tier"),
                "consecutive_windows": a.get("consecutive_windows"),
            }
            for a in top_assets
        ],
        "burst_events": [
            {
                "symbol": b.get("symbol"),
                "chain": next((a.get("chain") for a in recent if a.get("asset_key") == b.get("asset_key")), None),
                "contract": next((a.get("contract") for a in recent if a.get("asset_key") == b.get("asset_key")), None),
                "score": b.get("score"),
                "tier": b.get("tier"),
                "sources": json.loads(b.get("sources") or "[]"),
                "pushed_at_unix": b.get("pushed_at"),
                "is_upgrade": bool(b.get("is_upgrade")),
            }
            for b in bursts
        ],
    }
    return json.dumps(ctx, ensure_ascii=False, indent=2)


def _resolve_provider(cfg: dict) -> str:
    provider = (cfg.get("report_provider") or "auto").strip().lower()
    if provider in {"disabled", "off", "none"}:
        return "disabled"
    if provider in {"auto", "codex"}:
        return "codex"
    if shutil.which("codex"):
        return "codex"
    return "disabled"


def _call_codex(system_prompt: str, context_json: str, model: str | None = None) -> str:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError("codex command not found in PATH")

    prompt = (
        f"{system_prompt}\n\n"
        "Below is the monitoring data collected in the relevant time window. "
        "Write the final report body only. Do not explain your process, "
        "do not return JSON, and do not add any extra commentary outside the final Discord-ready text.\n\n"
        f"```json\n{context_json}\n```"
    )

    with tempfile.NamedTemporaryFile(prefix="codex-report-", suffix=".txt", delete=False) as tmp:
        output_path = Path(tmp.name)

    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
        "-C",
        str(Path(__file__).parent.parent.parent),
        "-",
    ]
    if model:
        cmd.extend(["--model", model])

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"codex exited with {proc.returncode}")
        if not output_path.exists():
            raise RuntimeError("codex did not write output file")
        result = output_path.read_text(encoding="utf-8").strip()
        if not result:
            raise RuntimeError("codex returned empty report")
        return result
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass


def run(cfg: dict, kind: str, dry_run: bool = False):
    state.init()

    webhook = cfg["discord"].get("webhook_reports", "")
    if not webhook or webhook.startswith("https://discord.com/api/webhooks/REPLACE"):
        logger.error("webhook_reports not configured in config.yaml")
        sys.exit(1)

    provider = _resolve_provider(cfg)

    # load prompt and context
    system_prompt = _load_prompt(kind)
    context_json = _build_context(kind)
    logger.info("context prepared: %d chars", len(context_json))

    if dry_run:
        logger.info("[DRY RUN] provider=%s; would generate and post the %s report", provider, kind)
        logger.debug("context:\n%s", context_json)
        return

    if provider == "disabled":
        logger.warning("report provider is disabled; skipping %s report", kind)
        return

    try:
        if provider == "codex":
            model = (cfg.get("codex_model") or "").strip() or None
            logger.info("calling local Codex CLI%s for %s report",
                        f" ({model})" if model else "", kind)
            report_text = _call_codex(system_prompt, context_json, model)
        else:
            logger.error("unknown report provider: %s", provider)
            sys.exit(1)
    except Exception as e:
        logger.error("%s provider error: %s", provider, e)
        sys.exit(1)

    # post to Discord
    ok = notifier.send_report_text(webhook, report_text, kind)
    if ok:
        logger.info("%s report posted successfully", kind)
    else:
        logger.error("failed to post %s report to Discord", kind)
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = cfg_mod.load(args.config)
    run(cfg, args.kind, dry_run=args.dry_run)
