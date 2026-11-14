"""Persistencia SQLite: snapshots con marca de tiempo para comparación histórica."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from backend import config
from backend.models.schemas import DisasterEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    external_id TEXT NOT NULL,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    occurred_at TEXT,
    magnitude REAL,
    depth_km REAL,
    raw TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_snapshot ON events(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);

CREATE TABLE IF NOT EXISTS infra_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_snapshot(source: str, events: list[DisasterEvent]) -> int:
    captured_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO snapshots (captured_at, source) VALUES (?, ?)",
            (captured_at, source),
        )
        snapshot_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO events
               (snapshot_id, external_id, source, category, title,
                lat, lon, occurred_at, magnitude, depth_km, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    e.id,
                    e.source,
                    e.category.value,
                    e.title,
                    e.lat,
                    e.lon,
                    e.occurred_at.isoformat() if e.occurred_at else None,
                    e.magnitude,
                    e.depth_km,
                    json.dumps(e.raw, ensure_ascii=False),
                )
                for e in events
            ],
        )
        conn.commit()
        return snapshot_id
    finally:
        conn.close()


def get_latest_events(source_filter: str = "live") -> list[dict]:
    """Último snapshot guardado; resguardo si las APIs externas fallan."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM snapshots WHERE source = ? ORDER BY id DESC LIMIT 1",
            (source_filter,),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return []
        return get_snapshot_events(row["id"])
    finally:
        conn.close()


def list_snapshots(source: Optional[str] = None, limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        if source:
            rows = conn.execute(
                """SELECT id, captured_at, source,
                          (SELECT COUNT(*) FROM events WHERE snapshot_id = snapshots.id)
                              AS event_count
                   FROM snapshots WHERE source = ?
                   ORDER BY id DESC LIMIT ?""",
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, captured_at, source,
                          (SELECT COUNT(*) FROM events WHERE snapshot_id = snapshots.id)
                              AS event_count
                   FROM snapshots ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_snapshot_events(snapshot_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE snapshot_id = ? ORDER BY id",
            (snapshot_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def compare_snapshots(id_a: int, id_b: int) -> dict:
    a = {r["external_id"]: r for r in get_snapshot_events(id_a)}
    b = {r["external_id"]: r for r in get_snapshot_events(id_b)}
    added = [b[k] for k in b if k not in a]
    removed = [a[k] for k in a if k not in b]
    return {
        "snapshot_a": id_a,
        "snapshot_b": id_b,
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added,
        "removed": removed,
    }


def save_infra_cache(cache_key: str, data: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO infra_cache (cache_key, data, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE
               SET data = excluded.data, fetched_at = excluded.fetched_at""",
            (cache_key, json.dumps(data, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def load_infra_cache(cache_key: str, max_age_hours: int = 24) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT data, fetched_at FROM infra_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        age = datetime.now(timezone.utc) - fetched
        if age.total_seconds() > max_age_hours * 3600:
            return None
        return json.loads(row["data"])
    finally:
        conn.close()

