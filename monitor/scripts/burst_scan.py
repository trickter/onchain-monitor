"""
Burst scan main script.

Run every 3 minutes (via Windows Task Scheduler or cron).
Pipeline:
  collect_all → score_events → hard filter → cooldown check → quiet hours → push
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from monitor.lib import config as cfg_mod
from monitor.lib import notifier, scorer, state
from monitor.scripts.collect_snapshot import collect_all

logger = logging.getLogger(__name__)


def _is_quiet_hours(cfg: dict) -> bool:
    """Returns True if current time is within quiet hours."""
    qh = cfg.get("quiet_hours", {})
    tz_name = qh.get("tz", "Asia/Shanghai")
    start_str = qh.get("start", "00:00")
    end_str = qh.get("end", "07:00")

    # support env override for testing
    import os
    now_override = os.environ.get("MONITOR_NOW")
    tz = pytz.timezone(tz_name)

    if now_override:
        now_time = datetime.strptime(now_override, "%H:%M").time()
    else:
        now_time = datetime.now(tz).time()

    start = datetime.strptime(start_str, "%H:%M").time()
    end = datetime.strptime(end_str, "%H:%M").time()

    if start <= end:
        return start <= now_time < end
    # crosses midnight
    return now_time >= start or now_time < end


def _build_explorer_urls(chain: str, contract: str) -> tuple[str, str]:
    explorer = cfg_mod.DEX_URLS.get(chain, "").format(contract)
    # DexScreener: https://dexscreener.com/{chain}/{contract}
    ds_chain = {"bsc": "bsc", "solana": "solana", "ethereum": "ethereum", "base": "base"}.get(chain, chain)
    dexscreener = f"https://dexscreener.com/{ds_chain}/{contract}"
    return explorer, dexscreener


def _load_watchlist() -> set[str]:
    wl_path = Path(__file__).parent.parent / "state" / "watchlist.json"
    if not wl_path.exists():
        return set()
    try:
        data = json.loads(wl_path.read_text())
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _summarize_asset(asset, reason: str) -> str:
    symbol = asset.symbol or asset.contract or asset.asset_key
    return (
        f"{symbol} [{asset.chain}] "
        f"score={asset.score} tier={asset.tier} reason={reason} "
        f"sources={','.join(asset.sources)}"
    )


def _write_latest_debug_snapshot(push_count: int, upgrade_count: int, blocked_summaries: list[str]) -> None:
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    debug_path = logs_dir / "latest-burst-debug.log"

    lines = [
        f"time: {datetime.now().isoformat()}",
        f"pushed: {push_count}",
        f"upgrades: {upgrade_count}",
    ]

    if blocked_summaries:
        lines.append("top_blocked_candidates:")
        lines.extend(f"- {summary}" for summary in blocked_summaries)
    else:
        lines.append("top_blocked_candidates: none")

    debug_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cfg: dict, dry_run: bool = False):
    state.init()

    # check global mute
    if state.is_globally_muted():
        logger.info("globally muted, skipping scan")
        return

    quiet = _is_quiet_hours(cfg)
    watchlist = _load_watchlist()

    # Step 1: collect
    logger.info("collecting snapshot …")
    events = collect_all(cfg)
    logger.info("total events: %d", len(events))

    # Step 2: refresh consecutive_windows BEFORE scoring so scorer sees current count
    unique_assets: dict[str, tuple[str, str, str]] = {}  # asset_key → (symbol, chain, contract)
    for ev in events:
        ak = ev["asset_key"]
        if ak not in unique_assets:
            unique_assets[ak] = (ev.get("symbol", ""), ev.get("chain", ""), ev.get("contract", ""))
    for ak, (sym, chain, contract) in unique_assets.items():
        state.refresh_consecutive(ak, sym, chain, contract)

    # Step 3: read fresh asset_states (consecutive_windows already incremented)
    asset_states: dict[str, dict] = {}
    for ak in unique_assets:
        row = state.get_asset(ak)
        if row:
            asset_states[ak] = row

    # Step 4: score
    scored = scorer.score_events(events, cfg, asset_states, watchlist)
    logger.info("scored assets: %d", len(scored))

    push_count = 0
    upgrade_count = 0
    blocked_summaries: list[str] = []

    for asset in scored:
        # persist score, tier, and delta-comparison metrics for the next scan
        state.upsert_asset(
            asset.asset_key, asset.symbol, asset.chain, asset.contract,
            asset.score, asset.tier,
            hype_rank=asset.metrics.get("hype_rank"),
            topic_inflow=asset.metrics.get("topic_net_inflow"),
        )

        # candidate (score 4-5) is report-only, never burst
        if asset.tier in ("noise", "candidate"):
            if len(blocked_summaries) < 5 and asset.score >= cfg.get("thresholds", {}).get("burst_report_candidate_min_score", 4):
                blocked_summaries.append(_summarize_asset(asset, "below burst threshold"))
            continue

        # risky-tagged assets (high_bundle, suspicious_dev, etc.) skip burst
        if asset.report_candidate_only:
            if len(blocked_summaries) < 5:
                blocked_summaries.append(_summarize_asset(asset, "report-only risk tag"))
            continue

        # burst requires at least one on-chain source
        if asset.tier in ("hot", "critical") and not asset.has_onchain_evidence:
            logger.debug("skip burst %s: no on-chain evidence", asset.asset_key)
            if len(blocked_summaries) < 5:
                blocked_summaries.append(_summarize_asset(asset, "missing on-chain evidence"))
            continue

        # quiet hours suppress hot (not critical)
        if quiet and asset.tier == "hot":
            logger.debug("quiet hours: suppressing hot burst for %s", asset.symbol)
            if len(blocked_summaries) < 5:
                blocked_summaries.append(_summarize_asset(asset, "quiet hours"))
            continue

        # cooldown check
        ok_to_push, is_upgrade = state.should_push(
            asset.asset_key, asset.score, asset.tier, asset.sources,
            cfg.get("cooldown_minutes", {}),
        )
        if not ok_to_push:
            logger.debug("cooldown: skip %s (score=%d, tier=%s)", asset.symbol, asset.score, asset.tier)
            if len(blocked_summaries) < 5:
                blocked_summaries.append(_summarize_asset(asset, "cooldown"))
            continue

        webhook = cfg["discord"]["webhook_bursts"]
        if not webhook or webhook.startswith("https://discord.com/api/webhooks/REPLACE"):
            logger.warning("webhook_bursts not configured")
            if len(blocked_summaries) < 5:
                blocked_summaries.append(_summarize_asset(asset, "webhook not configured"))
            continue

        explorer_url, dexscreener_url = _build_explorer_urls(asset.chain, asset.contract)

        embed = notifier.build_burst_embed(
            asset_key=asset.asset_key,
            symbol=asset.symbol,
            chain=asset.chain,
            contract=asset.contract,
            score=asset.score,
            tier=asset.tier,
            sources=asset.sources,
            evidence=asset.evidence,
            metrics=asset.metrics,
            explorer_url=explorer_url,
            dexscreener_url=dexscreener_url,
        )

        if dry_run:
            logger.info("[DRY RUN] would push %s (score=%d tier=%s upgrade=%s)",
                        asset.symbol, asset.score, asset.tier, is_upgrade)
            logger.debug("embed: %s", json.dumps(embed, ensure_ascii=False))
        else:
            ok = notifier.send_burst(webhook, embed)
            if ok:
                state.mark_pushed(asset.asset_key, asset.symbol, asset.score, asset.tier,
                                   asset.sources, is_upgrade)
                push_count += 1
                if is_upgrade:
                    upgrade_count += 1
                logger.info("pushed %s (score=%d tier=%s upgrade=%s)",
                            asset.symbol, asset.score, asset.tier, is_upgrade)
            else:
                logger.error("failed to push %s", asset.symbol)

    logger.info("scan complete: pushed=%d upgrades=%d", push_count, upgrade_count)
    if push_count == 0 and blocked_summaries:
        logger.info("top blocked candidates:")
        for summary in blocked_summaries:
            logger.info("  - %s", summary)
    _write_latest_debug_snapshot(push_count, upgrade_count, blocked_summaries)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="simulate without pushing")
    args = parser.parse_args()

    cfg = cfg_mod.load(args.config)
    run(cfg, dry_run=args.dry_run)
