"""Discord webhook sender with embed builder and 429 retry logic."""

import time
from typing import Any

import requests

# tier → embed color (Discord decimal)
TIER_COLORS = {
    "critical": 0xE74C3C,   # red
    "hot": 0xF39C12,        # orange
    "candidate": 0x3498DB,  # blue
    "report": 0x2ECC71,     # green
}

CHAIN_EMOJI = {
    "solana": "◎",
    "ethereum": "Ξ",
    "bsc": "⬡",
    "base": "🔵",
}


def _format_compact_number(value: float | int | None, decimals: int = 1) -> str:
    if value is None:
        return "-"
    n = float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    units = [
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ]
    for threshold, suffix in units:
        if n >= threshold:
            compact = n / threshold
            text = f"{compact:.{decimals}f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    if n.is_integer():
        return f"{sign}{int(n)}"
    return f"{sign}{n:.{decimals}f}".rstrip("0").rstrip(".")


def _post_with_retry(webhook_url: str, payload: dict, retries: int = 3) -> bool:
    for attempt in range(retries):
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 429:
            try:
                retry_after = resp.json().get("retry_after", 5)
            except Exception:
                retry_after = 5
            time.sleep(float(retry_after))
            continue
        # non-retryable
        resp.raise_for_status()
    return False


def build_burst_embed(
    asset_key: str,
    symbol: str,
    chain: str,
    contract: str,
    score: int,
    tier: str,
    sources: list[str],
    evidence: list[str],
    metrics: dict,
    explorer_url: str,
    dexscreener_url: str,
) -> dict:
    chain_icon = CHAIN_EMOJI.get(chain, "🔗")
    tier_label = {"critical": "🚨 CRITICAL", "hot": "🔥 HOT"}.get(tier, tier.upper())

    description_lines = ["**命中证据：**"] + [f"- {e}" for e in evidence]
    description = "\n".join(description_lines)

    fields: list[dict] = [
        {"name": "合约地址", "value": f"`{contract}`", "inline": False},
        {"name": "链", "value": f"{chain_icon} {chain.upper()}", "inline": True},
        {"name": "分数 / 级别", "value": f"{score} / {tier_label}", "inline": True},
    ]

    if metrics.get("price"):
        fields.append({"name": "价格", "value": f"${float(metrics['price']):.6g}", "inline": True})
    if metrics.get("market_cap"):
        fields.append({"name": "市值", "value": f"${_format_compact_number(metrics['market_cap'])}", "inline": True})
    if metrics.get("holders"):
        fields.append({"name": "持有者", "value": _format_compact_number(metrics["holders"]), "inline": True})
    if metrics.get("risk_control_level") is not None:
        level = int(metrics["risk_control_level"])
        risk_label = {0: "未知", 1: "低", 2: "中", 3: "中高", 4: "高", 5: "高(手动)"}
        fields.append({"name": "风险", "value": risk_label.get(level, str(level)), "inline": True})

    links = f"[DEX Screener]({dexscreener_url}) · [Explorer]({explorer_url})"

    return {
        "title": f"{symbol}  {chain_icon}",
        "description": description,
        "color": TIER_COLORS.get(tier, 0x95A5A6),
        "fields": fields,
        "footer": {"text": f"sources: {', '.join(sources)}  |  {links}"},
        "timestamp": _iso_now(),
    }


def send_burst(webhook_url: str, embed: dict) -> bool:
    return _post_with_retry(webhook_url, {"embeds": [embed]})


def send_report_text(webhook_url: str, content: str, kind: str) -> bool:
    """Send a plain markdown report (daily digest)."""
    label = {"morning": "☀️ 晨报", "noon": "🌤 午报", "evening": "🌙 晚报"}.get(kind, kind)
    # Discord message limit is 2000 chars; split if needed
    header = f"**{label}  {_date_str()}**\n\n"
    full = header + content
    chunks = _split_message(full, 2000)
    for chunk in chunks:
        ok = _post_with_retry(webhook_url, {"content": chunk})
        if not ok:
            return False
    return True


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _iso_now() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _date_str() -> str:
    import datetime
    return datetime.date.today().strftime("%Y-%m-%d")
