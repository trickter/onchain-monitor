"""SQLite state layer for dedup, cooldown, consecutive hit tracking, and mute."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_STATE_DIR = Path(__file__).parent.parent / "state"
_DB_PATH = _STATE_DIR / "pushes.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_key         TEXT PRIMARY KEY,
    symbol            TEXT,
    chain             TEXT,
    contract          TEXT,
    first_seen        INTEGER NOT NULL,
    last_seen         INTEGER NOT NULL,
    consecutive_windows INTEGER DEFAULT 1,
    last_score        INTEGER DEFAULT 0,
    last_tier         TEXT,
    last_pushed       INTEGER,
    last_push_sources TEXT,    -- JSON array
    last_push_score   INTEGER,
    last_hype_rank    INTEGER,
    last_topic_inflow REAL
);

CREATE TABLE IF NOT EXISTS push_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_key   TEXT NOT NULL,
    symbol      TEXT,
    score       INTEGER,
    tier        TEXT,
    sources     TEXT,          -- JSON array
    pushed_at   INTEGER NOT NULL,
    is_upgrade  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS global_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def _conn():
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with _conn() as con:
        con.executescript(_SCHEMA)
        # migrate existing databases that pre-date these columns
        for col, typedef in [("last_hype_rank", "INTEGER"), ("last_topic_inflow", "REAL")]:
            try:
                con.execute(f"ALTER TABLE assets ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists


def get_asset(asset_key: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM assets WHERE asset_key = ?", (asset_key,)
        ).fetchone()
    return dict(row) if row else None


def refresh_consecutive(asset_key: str, symbol: str, chain: str, contract: str) -> int:
    """
    Increment consecutive_windows for an asset seen in this scan window.
    Must be called BEFORE score_events so the scorer sees the updated count.
    Returns the new consecutive_windows value.
    """
    now = int(time.time())
    existing = get_asset(asset_key)
    with _conn() as con:
        if existing is None:
            con.execute(
                """INSERT INTO assets
                   (asset_key, symbol, chain, contract, first_seen, last_seen, consecutive_windows, last_score, last_tier)
                   VALUES (?, ?, ?, ?, ?, ?, 1, 0, 'unknown')""",
                (asset_key, symbol, chain, contract, now, now),
            )
            return 1
        else:
            gap_minutes = (now - existing["last_seen"]) / 60
            new_consecutive = existing["consecutive_windows"] + 1 if gap_minutes <= 10 else 1
            con.execute(
                "UPDATE assets SET symbol=?, last_seen=?, consecutive_windows=? WHERE asset_key=?",
                (symbol, now, new_consecutive, asset_key),
            )
            return new_consecutive


def upsert_asset(
    asset_key: str,
    symbol: str,
    chain: str,
    contract: str,
    score: int,
    tier: str,
    hype_rank: int | None = None,
    topic_inflow: float | None = None,
) -> None:
    """Update score, tier, and delta-comparison metrics after scoring."""
    with _conn() as con:
        con.execute(
            """UPDATE assets SET
               last_score = ?, last_tier = ?, last_hype_rank = ?, last_topic_inflow = ?
               WHERE asset_key = ?""",
            (score, tier, hype_rank, topic_inflow, asset_key),
        )


def mark_pushed(asset_key: str, symbol: str, score: int, tier: str, sources: list[str], is_upgrade: bool = False):
    now = int(time.time())
    sources_json = json.dumps(sources)
    with _conn() as con:
        con.execute(
            """UPDATE assets SET last_pushed = ?, last_push_sources = ?, last_push_score = ?
               WHERE asset_key = ?""",
            (now, sources_json, score, asset_key),
        )
        con.execute(
            """INSERT INTO push_log (asset_key, symbol, score, tier, sources, pushed_at, is_upgrade)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (asset_key, symbol, score, tier, sources_json, now, 1 if is_upgrade else 0),
        )


def should_push(
    asset_key: str,
    score: int,
    tier: str,
    new_sources: list[str],
    cooldown_minutes: dict,
) -> tuple[bool, bool]:
    """
    Returns (should_push, is_upgrade).
    Upgrade allowed within cooldown if score improved by >= delta AND new on-chain source added.
    """
    asset = get_asset(asset_key)
    if asset is None or asset["last_pushed"] is None:
        return True, False

    now = int(time.time())
    elapsed_minutes = (now - asset["last_pushed"]) / 60
    base_cd = cooldown_minutes.get("critical", 30) if tier == "critical" else cooldown_minutes.get("default", 90)

    if elapsed_minutes >= base_cd:
        return True, False

    # check upgrade condition inside cooldown
    prev_score = asset.get("last_push_score") or 0
    prev_sources = set(json.loads(asset.get("last_push_sources") or "[]"))
    onchain_sources = {"okx_signal", "smart_money_inflow"}
    new_onchain = bool(set(new_sources) & onchain_sources - prev_sources)
    score_delta = score - prev_score
    upgrade_delta = cooldown_minutes.get("upgrade_score_delta", 2)

    if score_delta >= upgrade_delta and new_onchain:
        return True, True

    return False, False


def is_globally_muted() -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT value FROM global_state WHERE key = 'mute_until'"
        ).fetchone()
    if row is None:
        return False
    return int(row["value"]) > int(time.time())


def set_global_mute(minutes: int):
    mute_until = int(time.time()) + minutes * 60
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO global_state (key, value) VALUES ('mute_until', ?)",
            (str(mute_until),),
        )


def recent_push_log(hours: int = 24) -> list[dict]:
    since = int(time.time()) - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM push_log WHERE pushed_at >= ? ORDER BY pushed_at DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_assets(hours: int = 24) -> list[dict]:
    """Assets seen in the last N hours, ordered by last_score desc."""
    since = int(time.time()) - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM assets WHERE last_seen >= ? ORDER BY last_score DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]
