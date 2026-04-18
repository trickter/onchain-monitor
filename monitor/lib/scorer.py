"""
Multi-signal scoring engine.

Input: raw event dicts from collect_snapshot.py plus previous asset state.
Output: one scored asset per unique asset_key for burst_scan.py to process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScoredAsset:
    asset_key: str
    symbol: str
    chain: str
    contract: str
    score: int
    tier: str  # "noise" | "candidate" | "hot" | "critical"
    sources: list[str]
    evidence: list[str]
    metrics: dict[str, Any]
    has_onchain_evidence: bool
    discard: bool = False
    report_candidate_only: bool = False


_BLACKLIST_TAG_MAP = {
    "honeypot",
    "rug",
    "blacklist",
    "trading_disabled",
}

_REPORT_ONLY_TAG_MAP = {
    "high_bundle",
    "suspicious_dev",
    "extreme_concentration",
}


def _check_hard_filter(merged: dict, cfg: dict) -> tuple[bool, bool]:
    """
    Returns (discard, report_candidate_only).

    discard=True means drop immediately.
    report_candidate_only=True means allow daily report candidates but skip burst.
    """
    thresholds = cfg.get("thresholds", {})
    blacklist_tags = set(cfg.get("blacklist_tags", _BLACKLIST_TAG_MAP))
    report_only_tags = set(cfg.get("report_candidate_only_tags", _REPORT_ONLY_TAG_MAP))

    token_tags = set(merged.get("token_tags", []))
    if token_tags & blacklist_tags:
        return True, False
    report_only = bool(token_tags & report_only_tags)

    liq = float(merged.get("liquidity_usd") or 0)
    holders = int(float(merged.get("holders") or 0))
    risk = int(float(merged.get("risk_control_level") or 0))

    min_liq = thresholds.get("liquidity_usd", 100_000)
    min_holders = thresholds.get("holders", 300)
    max_risk = thresholds.get("risk_control_level_max", 3)

    if liq > 0 and liq < min_liq:
        return True, False
    if holders > 0 and holders < min_holders:
        return True, False
    if risk > max_risk:
        return True, False

    return False, report_only


def _score_social_hype(event: dict, cfg: dict, prev_rank: int | None) -> tuple[int, str | None]:
    rank = event.get("metrics", {}).get("hype_rank")
    if rank is None:
        return 0, None

    cutoff = cfg["thresholds"].get("hype_rank_cutoff", 5)
    improvement = cfg["thresholds"].get("hype_rank_improvement", 5)

    if rank <= cutoff:
        return 2, f"Social Hype entered top {cutoff} (now rank {rank})"
    if prev_rank is not None and (prev_rank - rank) >= improvement:
        return 2, f"Social Hype rank improved by {prev_rank - rank} to #{rank}"
    return 0, None


def _score_trending(event: dict) -> tuple[int, str | None]:
    rank = event.get("metrics", {}).get("trending_rank")
    if rank is not None and rank <= 20:
        return 2, f"Binance Trending rank #{rank}"

    rank = event.get("metrics", {}).get("topsearch_rank")
    if rank is not None and rank <= 20:
        return 2, f"Binance Top Search rank #{rank}"

    return 0, None


def _score_topic_rush(event: dict, cfg: dict, prev_inflow: float | None) -> tuple[int, str | None]:
    inflow = float(event.get("metrics", {}).get("topic_net_inflow") or 0)
    threshold = cfg["thresholds"].get("topic_net_inflow_usd", 50_000)
    growth = cfg["thresholds"].get("topic_net_inflow_growth", 1.5)

    if inflow < threshold:
        return 0, None

    if prev_inflow is None or prev_inflow == 0 or (inflow / prev_inflow) >= growth:
        evidence = f"Topic Rush net inflow ${inflow:,.0f}"
        if prev_inflow:
            evidence += f" ({inflow / prev_inflow:.1f}x)"
        return 1, evidence

    return 0, None


def _score_smart_money_inflow(event: dict) -> tuple[int, str | None]:
    rank = event.get("metrics", {}).get("smart_money_inflow_rank")
    if rank is not None and rank <= 10:
        return 2, f"Smart Money inflow top 10 (rank #{rank})"
    return 0, None


def _score_okx_signal(event: dict, cfg: dict) -> tuple[int, str | None]:
    wallet_count = int(event.get("metrics", {}).get("signal_wallet_count") or 0)
    threshold = cfg["thresholds"].get("smart_money_cluster_count", 3)
    if wallet_count >= threshold:
        wallet_type = event.get("metrics", {}).get("signal_wallet_type", "Smart Money")
        return 2, f"OKX aggregated buy signal: {wallet_count} {wallet_type} wallets"
    return 0, None


def _score_consecutive(consecutive_windows: int) -> tuple[int, str | None]:
    if consecutive_windows >= 2:
        return 2, f"Hit in {consecutive_windows} consecutive scan windows"
    return 0, None


def _score_watchlist(asset_key: str, watchlist: set[str]) -> tuple[int, str | None]:
    if asset_key in watchlist:
        return 2, "Matched watchlist"
    return 0, None


ONCHAIN_SOURCES = {"okx_signal", "smart_money_inflow"}


def score_events(
    events: list[dict],
    cfg: dict,
    asset_states: dict[str, dict],
    watchlist: set[str] | None = None,
) -> list[ScoredAsset]:
    """
    Group events by asset_key, merge metrics, apply hard filters, and compute scores.
    """
    watchlist = watchlist or set()

    groups: dict[str, list[dict]] = {}
    for event in events:
        groups.setdefault(event["asset_key"], []).append(event)

    results: list[ScoredAsset] = []

    for asset_key, asset_events in groups.items():
        merged_metrics: dict[str, Any] = {}
        sources: list[str] = []

        for event in asset_events:
            source = event.get("source", "unknown")
            if source not in sources:
                sources.append(source)
            for key, value in (event.get("metrics") or {}).items():
                if value is not None and value != "" and value != 0:
                    merged_metrics[key] = value

        symbol = asset_events[0].get("symbol", "???")
        chain = asset_events[0].get("chain", "")
        contract = asset_events[0].get("contract", "")

        discard, report_only = _check_hard_filter(merged_metrics, cfg)
        if discard:
            continue

        prev_state = asset_states.get(asset_key, {})
        prev_hype_rank = prev_state.get("last_hype_rank")
        prev_topic_inflow = prev_state.get("last_topic_inflow")
        consecutive = prev_state.get("consecutive_windows", 1)

        total_score = 0
        evidence: list[str] = []
        has_onchain = False

        for event in asset_events:
            source = event.get("source", "")

            if source == "social_hype":
                points, evidence_text = _score_social_hype(event, cfg, prev_hype_rank)
            elif source in ("trending", "topsearch"):
                points, evidence_text = _score_trending(event)
            elif source == "topic_rush":
                points, evidence_text = _score_topic_rush(event, cfg, prev_topic_inflow)
            elif source == "smart_money_inflow":
                points, evidence_text = _score_smart_money_inflow(event)
                has_onchain = has_onchain or bool(points)
            elif source == "okx_signal":
                points, evidence_text = _score_okx_signal(event, cfg)
                has_onchain = has_onchain or bool(points)
            else:
                points, evidence_text = 0, None

            total_score += points
            if evidence_text:
                evidence.append(evidence_text)

        points, evidence_text = _score_consecutive(consecutive)
        total_score += points
        if evidence_text:
            evidence.append(evidence_text)

        points, evidence_text = _score_watchlist(asset_key, watchlist)
        total_score += points
        if evidence_text:
            evidence.append(evidence_text)

        thresholds = cfg.get("thresholds", {})
        hot_min = thresholds.get("burst_hot_min_score", 6)
        critical_min = thresholds.get("burst_critical_min_score", 8)
        candidate_min = thresholds.get("burst_report_candidate_min_score", 4)

        if total_score >= critical_min:
            tier = "critical"
        elif total_score >= hot_min:
            tier = "hot"
        elif total_score >= candidate_min:
            tier = "candidate"
        else:
            tier = "noise"

        results.append(
            ScoredAsset(
                asset_key=asset_key,
                symbol=symbol,
                chain=chain,
                contract=contract,
                score=total_score,
                tier=tier,
                sources=sources,
                evidence=evidence,
                metrics=merged_metrics,
                has_onchain_evidence=has_onchain,
                discard=False,
                report_candidate_only=report_only,
            )
        )

    results.sort(key=lambda item: item.score, reverse=True)
    return results
