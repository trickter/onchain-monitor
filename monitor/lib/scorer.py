"""
Multi-signal scoring engine.

Input:  list of raw Event dicts (from collect_snapshot)
        previous asset state (from state.py)
Output: list of ScoredAsset dicts ready for burst_scan to act on
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────

@dataclass
class ScoredAsset:
    asset_key: str
    symbol: str
    chain: str
    contract: str
    score: int
    tier: str                        # "noise" | "candidate" | "hot" | "critical"
    sources: list[str]
    evidence: list[str]              # human-readable reason strings for embed
    metrics: dict[str, Any]          # merged best-effort metrics
    has_onchain_evidence: bool       # must be True for burst
    discard: bool = False            # hard-filtered out entirely
    report_candidate_only: bool = False  # risky tags → daily report only, no burst


# ──────────────────────────────────────────────
# Hard filter helpers
# ──────────────────────────────────────────────

_BLACKLIST_TAG_MAP = {
    "honeypot", "rug", "blacklist", "trading_disabled",
}

_REPORT_ONLY_TAG_MAP = {
    "high_bundle", "suspicious_dev", "extreme_concentration",
}

# OKX tokenTags that map to our internal tags
_OKX_TO_INTERNAL_TAG = {
    "honeypot": "honeypot",
    "trading_disabled": "trading_disabled",
}


def _check_hard_filter(merged: dict, cfg: dict) -> tuple[bool, bool]:
    """
    Returns (discard, report_candidate_only).
    discard=True  → drop immediately.
    report_candidate_only=True → allow into daily report candidate, not burst.
    """
    thresholds = cfg.get("thresholds", {})
    blacklist_tags = set(cfg.get("blacklist_tags", _BLACKLIST_TAG_MAP))
    report_only_tags = set(cfg.get("report_candidate_only_tags", _REPORT_ONLY_TAG_MAP))

    # tag check
    token_tags = set(merged.get("token_tags", []))
    if token_tags & blacklist_tags:
        return True, False
    report_only = bool(token_tags & report_only_tags)

    # numeric filters
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


# ──────────────────────────────────────────────
# Scoring rules (pure functions, easy to unit-test)
# ──────────────────────────────────────────────

def _score_social_hype(event: dict, cfg: dict, prev_rank: int | None) -> tuple[int, str | None]:
    """Social Hype Leaderboard: new top-N entry or rank improvement."""
    rank = event.get("metrics", {}).get("hype_rank")
    if rank is None:
        return 0, None
    cutoff = cfg["thresholds"].get("hype_rank_cutoff", 5)
    improvement = cfg["thresholds"].get("hype_rank_improvement", 5)

    if rank <= cutoff:
        ev = f"Social Hype 进入前 {cutoff}（当前第 {rank} 名）"
        return 1, ev
    if prev_rank is not None and (prev_rank - rank) >= improvement:
        ev = f"Social Hype 名次提升 {prev_rank - rank} 位 → 第 {rank} 名"
        return 1, ev
    return 0, None


def _score_trending(event: dict) -> tuple[int, str | None]:
    """Unified Token Rank Trending / Top Search."""
    rank = event.get("metrics", {}).get("trending_rank")
    if rank is not None and rank <= 20:
        return 1, f"Binance Trending 第 {rank} 名"
    rank = event.get("metrics", {}).get("topsearch_rank")
    if rank is not None and rank <= 20:
        return 1, f"Binance Top Search 第 {rank} 名"
    return 0, None


def _score_topic_rush(event: dict, cfg: dict, prev_inflow: float | None) -> tuple[int, str | None]:
    """Topic Rush: net inflow threshold + growth factor."""
    inflow = float(event.get("metrics", {}).get("topic_net_inflow") or 0)
    threshold = cfg["thresholds"].get("topic_net_inflow_usd", 50_000)
    growth = cfg["thresholds"].get("topic_net_inflow_growth", 1.5)

    if inflow < threshold:
        return 0, None
    if prev_inflow is None or prev_inflow == 0 or (inflow / prev_inflow) >= growth:
        ev = f"Topic Rush 净流入 ${inflow:,.0f}"
        if prev_inflow:
            ev += f"（↑{inflow/prev_inflow:.1f}x）"
        return 2, ev
    return 0, None


def _score_smart_money_inflow(event: dict) -> tuple[int, str | None]:
    """Smart Money Inflow Rank: top 10 entry."""
    rank = event.get("metrics", {}).get("smart_money_inflow_rank")
    if rank is not None and rank <= 10:
        return 2, f"Smart Money 净流入榜第 {rank} 名"
    return 0, None


def _score_okx_signal(event: dict, cfg: dict) -> tuple[int, str | None]:
    """OKX aggregated buy signal: >= N distinct wallets in window."""
    wallet_count = int(event.get("metrics", {}).get("signal_wallet_count") or 0)
    threshold = cfg["thresholds"].get("smart_money_cluster_count", 3)
    if wallet_count >= threshold:
        wallet_type = event.get("metrics", {}).get("signal_wallet_type", "聪明钱")
        return 3, f"OKX 聚合信号：{wallet_count} 个{wallet_type}地址买入"
    return 0, None


def _score_consecutive(consecutive_windows: int) -> tuple[int, str | None]:
    """Bonus for appearing in 2+ consecutive scan windows."""
    if consecutive_windows >= 2:
        return 2, f"连续 {consecutive_windows} 个扫描窗口命中"
    return 0, None


def _score_watchlist(asset_key: str, watchlist: set[str]) -> tuple[int, str | None]:
    if asset_key in watchlist:
        return 2, "watchlist 追踪资产命中"
    return 0, None


# ──────────────────────────────────────────────
# Main aggregation
# ──────────────────────────────────────────────

ONCHAIN_SOURCES = {"okx_signal", "smart_money_inflow"}


def score_events(
    events: list[dict],
    cfg: dict,
    asset_states: dict[str, dict],   # asset_key → state row from state.py
    watchlist: set[str] | None = None,
) -> list[ScoredAsset]:
    """
    Group events by asset_key, merge metrics, apply hard filters, compute score.
    Returns one ScoredAsset per unique asset_key.
    """
    watchlist = watchlist or set()

    # group by asset_key
    groups: dict[str, list[dict]] = {}
    for ev in events:
        key = ev["asset_key"]
        groups.setdefault(key, []).append(ev)

    results: list[ScoredAsset] = []

    for asset_key, evs in groups.items():
        # merge metrics from all sources (last-write wins, prefer non-None)
        merged_metrics: dict[str, Any] = {}
        sources: list[str] = []
        for ev in evs:
            src = ev.get("source", "unknown")
            if src not in sources:
                sources.append(src)
            for k, v in (ev.get("metrics") or {}).items():
                if v is not None and v != "" and v != 0:
                    merged_metrics[k] = v

        # representative fields
        symbol = evs[0].get("symbol", "???")
        chain = evs[0].get("chain", "")
        contract = evs[0].get("contract", "")

        # hard filter
        discard, report_only = _check_hard_filter(merged_metrics, cfg)
        if discard:
            continue

        # retrieve previous state for rank comparison
        prev_state = asset_states.get(asset_key, {})
        prev_hype_rank = prev_state.get("last_hype_rank")
        prev_topic_inflow = prev_state.get("last_topic_inflow")
        consecutive = prev_state.get("consecutive_windows", 1)

        # score accumulation
        total_score = 0
        evidence: list[str] = []
        has_onchain = False

        for ev in evs:
            src = ev.get("source", "")
            if src == "social_hype":
                pts, ev_str = _score_social_hype(ev, cfg, prev_hype_rank)
            elif src in ("trending", "topsearch"):
                pts, ev_str = _score_trending(ev)
            elif src == "topic_rush":
                pts, ev_str = _score_topic_rush(ev, cfg, prev_topic_inflow)
            elif src == "smart_money_inflow":
                pts, ev_str = _score_smart_money_inflow(ev)
                has_onchain = has_onchain or bool(pts)
            elif src == "okx_signal":
                pts, ev_str = _score_okx_signal(ev, cfg)
                has_onchain = has_onchain or bool(pts)
            else:
                pts, ev_str = 0, None

            total_score += pts
            if ev_str:
                evidence.append(ev_str)

        # consecutive bonus
        pts, ev_str = _score_consecutive(consecutive)
        total_score += pts
        if ev_str:
            evidence.append(ev_str)

        # watchlist bonus
        pts, ev_str = _score_watchlist(asset_key, watchlist)
        total_score += pts
        if ev_str:
            evidence.append(ev_str)

        # determine tier
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

    results.sort(key=lambda x: x.score, reverse=True)
    return results
