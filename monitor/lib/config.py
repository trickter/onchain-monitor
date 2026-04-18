import os
import yaml
from pathlib import Path

_MONITOR_DIR = Path(__file__).parent.parent

_DEFAULTS: dict = {
    "discord": {"webhook_reports": "", "webhook_bursts": ""},
    "chains": ["solana", "bsc", "base", "ethereum"],
    "thresholds": {
        "liquidity_usd": 100000,
        "holders": 300,
        "risk_control_level_max": 3,
        "topic_net_inflow_usd": 50000,
        "topic_net_inflow_growth": 1.5,
        "smart_money_cluster_count": 3,
        "smart_money_cluster_window_min": 15,
        "hype_rank_cutoff": 5,
        "hype_rank_improvement": 5,
        "burst_hot_min_score": 6,
        "burst_critical_min_score": 8,
        "burst_report_candidate_min_score": 4,
    },
    "cooldown_minutes": {"default": 90, "critical": 30, "upgrade_score_delta": 2},
    "quiet_hours": {"start": "00:00", "end": "07:00", "tz": "Asia/Shanghai"},
    "onchainos_bin": "onchainos",
    "scan_interval_minutes": 3,
    "report_provider": "codex",
    "codex_model": "",
    "blacklist_tags": ["honeypot", "rug", "blacklist", "trading_disabled"],
    "report_candidate_only_tags": ["high_bundle", "suspicious_dev", "extreme_concentration"],
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else _MONITOR_DIR / "config.yaml"
    cfg = _deep_merge(_DEFAULTS, {})
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user_cfg)

    # env var overrides
    for env, keys in [
        ("DISCORD_WEBHOOK_REPORTS", ["discord", "webhook_reports"]),
        ("DISCORD_WEBHOOK_BURSTS", ["discord", "webhook_bursts"]),
        ("REPORT_PROVIDER", ["report_provider"]),
        ("CODEX_MODEL", ["codex_model"]),
    ]:
        val = os.environ.get(env)
        if val:
            node = cfg
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = val

    return cfg


# Binance chainId ↔ OKX CLI chain name mapping
CHAIN_CLI: dict[str, str] = {
    "solana": "solana",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "base": "base",
}

# OKX chainIndex (from API response) → canonical chain name
OKX_INDEX_TO_CHAIN: dict[str, str] = {
    "501": "solana",
    "1": "ethereum",
    "56": "bsc",
    "8453": "base",
}

# Binance chainId → canonical chain name
BINANCE_CHAIN_ID: dict[str, str] = {
    "CT_501": "solana",
    "1": "ethereum",
    "56": "bsc",
    "8453": "base",
}

# canonical chain name → Binance chainId
CHAIN_TO_BINANCE: dict[str, str] = {v: k for k, v in BINANCE_CHAIN_ID.items()}

# DEX explorer URL builders
DEX_URLS: dict[str, str] = {
    "solana": "https://solscan.io/token/{}",
    "ethereum": "https://etherscan.io/token/{}",
    "bsc": "https://bscscan.com/token/{}",
    "base": "https://basescan.org/token/{}",
}

DEXSCREENER_URL = "https://dexscreener.com/{}/{}?chain={}"
