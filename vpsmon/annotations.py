"""Deploy annotations — mark events on the timeline via webhook.

POST /api/annotations with a JSON body to record deploys, releases,
config changes, etc. Shows on charts and in the audit log.
"""
import time
import logging

from . import db

log = logging.getLogger(__name__)


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS annotations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          INTEGER NOT NULL,
        title       TEXT NOT NULL,
        description TEXT,
        tags        TEXT,
        source      TEXT,
        url         TEXT,
        created_by  TEXT
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_annotations_ts ON annotations(ts DESC)")


async def create(title: str, description: str = "", tags: str = "",
                 source: str = "", url: str = "", created_by: str = "") -> int:
    await _ensure_schema()
    now = int(time.time())
    await db.execute(
        "INSERT INTO annotations (ts, title, description, tags, source, url, created_by) VALUES (?,?,?,?,?,?,?)",
        (now, title, description, tags, source, url, created_by),
    )
    rows = await db.execute("SELECT id FROM annotations WHERE ts = ? AND title = ? ORDER BY id DESC LIMIT 1",
                           (now, title))
    return rows[0]["id"] if rows else 0


async def list_annotations(hours: int = 168, limit: int = 100) -> list:
    await _ensure_schema()
    cutoff = int(time.time()) - hours * 3600
    return await db.execute(
        "SELECT * FROM annotations WHERE ts > ? ORDER BY ts DESC LIMIT ?",
        (cutoff, limit),
    )


async def delete(annotation_id: int):
    await db.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
