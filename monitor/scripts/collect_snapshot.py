"""
Data collection layer.

Calls all 7 data sources and returns a flat list of normalized Event dicts.
Each event represents one signal from one source for one asset.

Event schema:
{
    "asset_key":    "{chain}:{contract}",
    "chain":        "solana" | "ethereum" | "bsc" | "base",
    "contract":     str,
    "symbol":       str,
    "name":         str,
    "source":       "social_hype" | "trending" | "topsearch" | "topic_rush"
                    | "smart_money_inflow" | "okx_signal" | "okx_hot",
    "observed_at":  ISO-8601 string,
    "metrics":      dict,   # source-specific numeric fields
    "raw_ref":      dict,   # original response snippet for debugging
}
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# allow running as script: add monitor/ to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from monitor.lib import config as cfg_mod

logger = logging.getLogger(__name__)

BINANCE_UA = "binance-web3/2.1 (Skill)"
BINANCE_HEADERS = {"Accept-Encoding": "identity", "User-Agent": BINANCE_UA}
REQUEST_TIMEOUT = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_cli(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    return int(f) if f is not None else None


# ──────────────────────────────────────────────
# Source 1: Binance Social Hype Leaderboard
# ──────────────────────────────────────────────

_SOCIAL_HYPE_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market"
    "/token/pulse/social/hype/rank/leaderboard/ai"
)

# Chains supported by Social Hype API
_SOCIAL_HYPE_CHAINS = ["CT_501", "56", "8453"]


def fetch_social_hype(cfg: dict) -> list[dict]:
    events: list[dict] = []
    for binance_chain in _SOCIAL_HYPE_CHAINS:
        canonical = cfg_mod.BINANCE_CHAIN_ID.get(binance_chain)
        if canonical not in cfg.get("chains", []):
            continue
        try:
            resp = requests.get(
                _SOCIAL_HYPE_URL,
                params={
                    "chainId": binance_chain,
                    "sentiment": "All",
                    "socialLanguage": "ALL",
                    "targetLanguage": "zh",
                    "timeRange": 1,
                },
                headers=BINANCE_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("leaderBoardList", [])
            for rank, item in enumerate(items, start=1):
                meta = item.get("metaInfo", {})
                contract = meta.get("contractAddress", "")
                if not contract:
                    continue
                asset_key = f"{canonical}:{contract}"
                market = item.get("marketInfo", {})
                social = item.get("socialHypeInfo", {})
                events.append({
                    "asset_key": asset_key,
                    "chain": canonical,
                    "contract": contract,
                    "symbol": meta.get("symbol", ""),
                    "name": meta.get("symbol", ""),
                    "source": "social_hype",
                    "observed_at": _now_iso(),
                    "metrics": {
                        "hype_rank": rank,
                        "hype_score": _safe_float(social.get("socialHype")),
                        "sentiment": social.get("sentiment"),
                        "price_change_24h": _safe_float(market.get("priceChange")),
                        "market_cap": _safe_float(market.get("marketCap")),
                        "summary": social.get("socialSummaryBriefTranslated") or social.get("socialSummaryBrief"),
                    },
                    "raw_ref": {"rank": rank, "chain": binance_chain},
                })
        except Exception as e:
            logger.warning("social_hype fetch failed (chain=%s): %s", binance_chain, e)
    return events


# ──────────────────────────────────────────────
# Source 2 & 3: Binance Unified Token Rank (Trending + Top Search)
# ──────────────────────────────────────────────

_UNIFIED_RANK_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market"
    "/token/pulse/unified/rank/list/ai"
)


def _fetch_unified_rank(rank_type: int, source_name: str, chains: list[str], cfg: dict) -> list[dict]:
    events: list[dict] = []
    for chain in chains:
        binance_id = cfg_mod.CHAIN_TO_BINANCE.get(chain)
        if not binance_id:
            continue
        try:
            resp = requests.post(
                _UNIFIED_RANK_URL,
                json={"rankType": rank_type, "chainId": binance_id, "period": 50, "page": 1, "size": 30},
                headers={**BINANCE_HEADERS, "Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            tokens = resp.json().get("data", {}).get("tokens", [])
            for rank, tok in enumerate(tokens, start=1):
                contract = tok.get("contractAddress", "")
                if not contract:
                    continue
                asset_key = f"{chain}:{contract}"
                rank_field = "trending_rank" if source_name == "trending" else "topsearch_rank"
                events.append({
                    "asset_key": asset_key,
                    "chain": chain,
                    "contract": contract,
                    "symbol": tok.get("symbol", ""),
                    "name": tok.get("symbol", ""),
                    "source": source_name,
                    "observed_at": _now_iso(),
                    "metrics": {
                        rank_field: rank,
                        "price": _safe_float(tok.get("price")),
                        "market_cap": _safe_float(tok.get("marketCap")),
                        "liquidity_usd": _safe_float(tok.get("liquidity")),
                        "holders": _safe_int(tok.get("holders")),
                        "volume_24h": _safe_float(tok.get("volume24h")),
                        "price_change_24h": _safe_float(tok.get("percentChange24h")),
                    },
                    "raw_ref": {"rank": rank, "rankType": rank_type},
                })
        except Exception as e:
            logger.warning("%s fetch failed (chain=%s): %s", source_name, chain, e)
    return events


def fetch_trending(cfg: dict) -> list[dict]:
    return _fetch_unified_rank(10, "trending", cfg.get("chains", []), cfg)


def fetch_topsearch(cfg: dict) -> list[dict]:
    return _fetch_unified_rank(11, "topsearch", cfg.get("chains", []), cfg)


# ──────────────────────────────────────────────
# Source 4: Binance Topic Rush
# ──────────────────────────────────────────────

_TOPIC_RUSH_URL = (
    "https://web3.binance.com/bapi/defi/v2/public/wallet-direct/buw/wallet/market"
    "/token/social-rush/rank/list/ai"
)

_TOPIC_CHAINS = ["CT_501", "56"]  # only Solana and BSC supported


def fetch_topic_rush(cfg: dict) -> list[dict]:
    events: list[dict] = []
    for binance_chain in _TOPIC_CHAINS:
        canonical = cfg_mod.BINANCE_CHAIN_ID.get(binance_chain)
        if canonical not in cfg.get("chains", []):
            continue
        for rank_type in [10, 20]:  # Latest + Rising
            try:
                resp = requests.get(
                    _TOPIC_RUSH_URL,
                    params={"chainId": binance_chain, "rankType": rank_type, "sort": 20, "asc": "false"},
                    headers=BINANCE_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                topics = resp.json().get("data", [])
                for topic in topics:
                    topic_id = topic.get("topicId", "")
                    topic_inflow = _safe_float(topic.get("topicNetInflow"))
                    topic_name = (topic.get("name") or {}).get("topicNameEn") or topic_id
                    for tok in topic.get("tokenList", []):
                        contract = tok.get("contractAddress", "")
                        if not contract:
                            continue
                        tok_chain = cfg_mod.BINANCE_CHAIN_ID.get(tok.get("chainId", binance_chain), canonical)
                        asset_key = f"{tok_chain}:{contract}"
                        events.append({
                            "asset_key": asset_key,
                            "chain": tok_chain,
                            "contract": contract,
                            "symbol": tok.get("symbol", ""),
                            "name": tok.get("symbol", ""),
                            "source": "topic_rush",
                            "observed_at": _now_iso(),
                            "metrics": {
                                "topic_id": topic_id,
                                "topic_name": topic_name,
                                "topic_net_inflow": topic_inflow,
                                "topic_net_inflow_1h": _safe_float(topic.get("topicNetInflow1h")),
                                "token_net_inflow": _safe_float(tok.get("netInflow")),
                                "market_cap": _safe_float(tok.get("marketCap")),
                                "liquidity_usd": _safe_float(tok.get("liquidity")),
                                "holders": _safe_int(tok.get("holders")),
                                "price_change_24h": _safe_float(tok.get("priceChange24h")),
                            },
                            "raw_ref": {"topicId": topic_id, "rankType": rank_type},
                        })
            except Exception as e:
                logger.warning("topic_rush fetch failed (chain=%s, type=%s): %s", binance_chain, rank_type, e)
    return events


# ──────────────────────────────────────────────
# Source 5: Binance Smart Money Inflow Rank
# ──────────────────────────────────────────────

_SMART_MONEY_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/tracker/wallet"
    "/token/inflow/rank/query/ai"
)

_SMART_MONEY_CHAINS = ["56", "CT_501"]


def fetch_smart_money_inflow(cfg: dict) -> list[dict]:
    events: list[dict] = []
    for binance_chain in _SMART_MONEY_CHAINS:
        canonical = cfg_mod.BINANCE_CHAIN_ID.get(binance_chain)
        if canonical not in cfg.get("chains", []):
            continue
        try:
            resp = requests.post(
                _SMART_MONEY_URL,
                json={"chainId": binance_chain, "period": "24h", "tagType": 2},
                headers={**BINANCE_HEADERS, "Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
            for rank, item in enumerate(items, start=1):
                contract = item.get("ca", "")
                if not contract:
                    continue
                asset_key = f"{canonical}:{contract}"
                token_tags = _extract_binance_tags(item)
                events.append({
                    "asset_key": asset_key,
                    "chain": canonical,
                    "contract": contract,
                    "symbol": item.get("tokenName", ""),
                    "name": item.get("tokenName", ""),
                    "source": "smart_money_inflow",
                    "observed_at": _now_iso(),
                    "metrics": {
                        "smart_money_inflow_rank": rank,
                        "smart_money_inflow_usd": _safe_float(item.get("inflow")),
                        "smart_money_traders": _safe_int(item.get("traders")),
                        "price": _safe_float(item.get("price")),
                        "market_cap": _safe_float(item.get("marketCap")),
                        "liquidity_usd": _safe_float(item.get("liquidity")),
                        "holders": _safe_int(item.get("holders")),
                        "risk_control_level": _safe_int(item.get("tokenRiskLevel")),
                        "token_tags": token_tags,
                    },
                    "raw_ref": {"rank": rank},
                })
        except Exception as e:
            logger.warning("smart_money_inflow fetch failed (chain=%s): %s", binance_chain, e)
    return events


def _extract_binance_tags(item: dict) -> list[str]:
    tags: list[str] = []
    tag_obj = item.get("tokenTag") or {}
    for category_tags in tag_obj.values():
        if isinstance(category_tags, list):
            tags.extend(str(t) for t in category_tags)
    return tags


# ──────────────────────────────────────────────
# Source 6: OKX onchainos signal list (per chain)
# ──────────────────────────────────────────────

_WALLET_TYPE_LABEL = {"1": "Smart Money", "2": "KOL", "3": "Whale"}


def fetch_okx_signal(cfg: dict) -> list[dict]:
    events: list[dict] = []
    onchainos = cfg.get("onchainos_bin", "onchainos")
    cluster_window = cfg["thresholds"].get("smart_money_cluster_window_min", 15)
    min_wallets = cfg["thresholds"].get("smart_money_cluster_count", 3)

    for chain in cfg.get("chains", []):
        cli_chain = cfg_mod.CHAIN_CLI.get(chain)
        if not cli_chain:
            continue
        try:
            result = _run_cli(
                [onchainos, "signal", "list", "--chain", cli_chain, "--wallet-type", "1",
                 "--limit", "50", "--min-address-count", str(min_wallets)],
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning("okx signal list failed (chain=%s): %s", chain, result.stderr[:200])
                continue
            signals = _parse_onchainos_json(result.stdout)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            window_ms = cluster_window * 60 * 1000
            for sig in signals:
                ts = _safe_int(sig.get("timestamp")) or 0
                if now_ms - ts > window_ms:
                    continue  # outside time window
                token = sig.get("token") or {}
                contract = token.get("tokenAddress", "")
                if not contract:
                    continue
                asset_key = f"{chain}:{contract}"
                wallet_count = _safe_int(sig.get("triggerWalletCount")) or 0
                wallet_type_code = sig.get("walletType", "1")
                wallet_label = _WALLET_TYPE_LABEL.get(str(wallet_type_code), "聪明钱")
                okx_tags = _extract_okx_signal_tags(sig)
                events.append({
                    "asset_key": asset_key,
                    "chain": chain,
                    "contract": contract,
                    "symbol": token.get("symbol", ""),
                    "name": token.get("name", ""),
                    "source": "okx_signal",
                    "observed_at": _now_iso(),
                    "metrics": {
                        "signal_wallet_count": wallet_count,
                        "signal_wallet_type": wallet_label,
                        "signal_amount_usd": _safe_float(sig.get("amountUsd")),
                        "signal_sold_ratio": _safe_float(sig.get("soldRatioPercent")),
                        "price": _safe_float(sig.get("price")),
                        "market_cap": _safe_float(token.get("marketCapUsd")),
                        "holders": _safe_int(token.get("holders")),
                        "token_tags": okx_tags,
                    },
                    "raw_ref": {"walletType": wallet_type_code, "timestamp": ts},
                })
        except FileNotFoundError:
            logger.error("onchainos binary not found: %s", onchainos)
            break
        except Exception as e:
            logger.warning("okx_signal fetch failed (chain=%s): %s", chain, e)
    return events


def _extract_okx_signal_tags(sig: dict) -> list[str]:
    """Map OKX token tags to internal tag names."""
    tags: list[str] = []
    raw_tags = sig.get("token", {}).get("tokenTags", []) if isinstance(sig.get("token"), dict) else []
    for tag in raw_tags:
        t = str(tag).lower()
        if "honeypot" in t:
            tags.append("honeypot")
        elif "trading_disabled" in t:
            tags.append("trading_disabled")
    return tags


def _parse_onchainos_json(stdout: str) -> list[dict]:
    """
    onchainos outputs either a JSON array directly, or a pretty-printed table.
    Try to parse JSON; if it fails return empty list and let caller handle.
    """
    stdout = stdout.strip()
    if not stdout:
        return []
    # Try to find a JSON array in the output
    start = stdout.find("[")
    end = stdout.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(stdout[start : end + 1])
        except json.JSONDecodeError:
            pass
    # Try full parse
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("data", "result", "list", "items"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
    except json.JSONDecodeError:
        pass
    logger.debug("Could not parse onchainos output as JSON: %.200s", stdout)
    return []


# ──────────────────────────────────────────────
# Source 7: OKX hot-tokens (supplementary)
# ──────────────────────────────────────────────

def fetch_okx_hot_tokens(cfg: dict) -> list[dict]:
    events: list[dict] = []
    onchainos = cfg.get("onchainos_bin", "onchainos")
    min_liq = cfg["thresholds"].get("liquidity_usd", 100_000)
    min_holders = cfg["thresholds"].get("holders", 300)

    for chain in cfg.get("chains", []):
        cli_chain = cfg_mod.CHAIN_CLI.get(chain)
        if not cli_chain:
            continue
        try:
            result = _run_cli(
                [
                    onchainos, "token", "hot-tokens",
                    "--ranking-type", "4",
                    "--chain", cli_chain,
                    "--limit", "30",
                    "--liquidity-min", str(int(min_liq)),
                    "--holders-min", str(int(min_holders)),
                    "--risk-filter", "true",
                ],
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning("okx hot-tokens failed (chain=%s): %s", chain, result.stderr[:200])
                continue
            tokens = _parse_onchainos_json(result.stdout)
            for tok in tokens:
                contract = tok.get("tokenContractAddress", "")
                if not contract:
                    continue
                asset_key = f"{chain}:{contract}"
                # map OKX chainIndex back to canonical
                okx_chain_idx = tok.get("chainIndex", "")
                canonical_chain = cfg_mod.OKX_INDEX_TO_CHAIN.get(okx_chain_idx, chain)
                risk_raw = tok.get("riskLevelControl")
                risk = _safe_int(risk_raw) if risk_raw else None
                events.append({
                    "asset_key": f"{canonical_chain}:{contract}",
                    "chain": canonical_chain,
                    "contract": contract,
                    "symbol": tok.get("tokenSymbol", ""),
                    "name": tok.get("tokenSymbol", ""),
                    "source": "okx_hot",
                    "observed_at": _now_iso(),
                    "metrics": {
                        "price": _safe_float(tok.get("price")),
                        "market_cap": _safe_float(tok.get("marketCap")),
                        "liquidity_usd": _safe_float(tok.get("liquidity")),
                        "holders": _safe_int(tok.get("holders")),
                        "volume_24h": _safe_float(tok.get("volume")),
                        "price_change_24h": _safe_float(tok.get("change")),
                        "risk_control_level": risk,
                        "inflow_usd": _safe_float(tok.get("inflowUsd")),
                    },
                    "raw_ref": {},
                })
        except FileNotFoundError:
            logger.error("onchainos binary not found: %s", onchainos)
            break
        except Exception as e:
            logger.warning("okx_hot fetch failed (chain=%s): %s", chain, e)
    return events


# ──────────────────────────────────────────────
# Main entry
# ──────────────────────────────────────────────

def collect_all(cfg: dict) -> list[dict]:
    """Collect events from all sources. Returns merged flat list."""
    all_events: list[dict] = []

    fetchers = [
        ("social_hype", fetch_social_hype),
        ("trending", fetch_trending),
        ("topsearch", fetch_topsearch),
        ("topic_rush", fetch_topic_rush),
        ("smart_money_inflow", fetch_smart_money_inflow),
        ("okx_signal", fetch_okx_signal),
        ("okx_hot", fetch_okx_hot_tokens),
    ]

    for name, fetcher in fetchers:
        try:
            events = fetcher(cfg)
            logger.info("%-20s → %d events", name, len(events))
            all_events.extend(events)
        except Exception as e:
            logger.error("fetcher %s raised: %s", name, e)

    return all_events


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = cfg_mod.load()
    events = collect_all(cfg)
    print(json.dumps(events, ensure_ascii=False, indent=2))
