"""Incident timeline — auto-records when alerts fire & resolve."""
import time
from typing import Optional

from . import db


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS incidents (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        key         TEXT NOT NULL,
        severity    TEXT NOT NULL,
        title       TEXT NOT NULL,
        message     TEXT,
        started_at  INTEGER NOT NULL,
        ended_at    INTEGER,
        duration    INTEGER,
        resolved    INTEGER DEFAULT 0,
        acked_by    TEXT,
        postmortem  TEXT
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_started ON incidents(started_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_key_open ON incidents(key, resolved)")


async def open_incident(key: str, severity: str, title: str, message: str = "") -> int:
    """Open a new incident if one isn't already open for this key."""
    await _ensure_schema()
    existing = await db.execute(
        "SELECT id FROM incidents WHERE key = ? AND resolved = 0 LIMIT 1",
        (key,),
    )
    if existing:
        return existing[0]["id"]
    now = int(time.time())
    await db.execute(
        "INSERT INTO incidents (key, severity, title, message, started_at) VALUES (?,?,?,?,?)",
        (key, severity, title, message, now),
    )
    rows = await db.execute("SELECT id FROM incidents WHERE key = ? AND started_at = ? ORDER BY id DESC LIMIT 1",
                           (key, now))
    return rows[0]["id"] if rows else 0


async def close_incident(key: str):
    """Mark any open incident for this key as resolved."""
    now = int(time.time())
    rows = await db.execute(
        "SELECT id, started_at FROM incidents WHERE key = ? AND resolved = 0",
        (key,),
    )
    for r in rows:
        duration = now - r["started_at"]
        await db.execute(
            "UPDATE incidents SET ended_at = ?, duration = ?, resolved = 1 WHERE id = ?",
            (now, duration, r["id"]),
        )


async def list_incidents(days: int = 30, limit: int = 200) -> list:
    await _ensure_schema()
    cutoff = int(time.time()) - days * 86400
    return await db.execute(
        "SELECT * FROM incidents WHERE started_at > ? ORDER BY started_at DESC LIMIT ?",
        (cutoff, limit),
    )


async def open_incidents() -> list:
    await _ensure_schema()
    return await db.execute(
        "SELECT * FROM incidents WHERE resolved = 0 ORDER BY started_at DESC"
    )


async def ack_incident(incident_id: int, username: str):
    await db.execute("UPDATE incidents SET acked_by = ? WHERE id = ?", (username, incident_id))


async def add_postmortem(incident_id: int, text: str):
    await db.execute("UPDATE incidents SET postmortem = ? WHERE id = ?", (text, incident_id))


async def summary(days: int = 7) -> dict:
    cutoff = int(time.time()) - days * 86400
    await _ensure_schema()
    all_rows = await db.execute(
        "SELECT severity, duration, resolved FROM incidents WHERE started_at > ?",
        (cutoff,),
    )
    total = len(all_rows)
    open_count = sum(1 for r in all_rows if not r["resolved"])
    critical = sum(1 for r in all_rows if r["severity"] == "critical")
    durations = [r["duration"] for r in all_rows if r.get("duration")]
    mttr = round(sum(durations) / len(durations) / 60, 1) if durations else None  # minutes
    return {
        "days": days,
        "total": total,
        "open": open_count,
        "critical": critical,
        "mttr_minutes": mttr,
    }


async def handle_alert(alert: dict):
    """Bridge: when an alert fires, open/close an incident."""
    try:
        severity = alert.get("severity", "warning")
        metric = alert.get("metric_name") or alert.get("metric") or "unknown"
        key = f"{alert.get('alert_type','')}:{metric}"
        message = alert.get("message", "")
        value = alert.get("current_value") or alert.get("value")
        # Uptime recovery alerts = severity info → close the matching incident
        if severity == "info" and "recover" in (message or "").lower():
            # Attempt to close same-key incident
            await close_incident(key)
            return
        # Otherwise open an incident
        title = f"[{severity.upper()}] {metric}"
        if value is not None:
            title += f" = {value}"
        await open_incident(key, severity, title, message)
    except Exception:
        pass
