"""Alert routing rules — control which alerts go to which notification channels."""
import json
import logging
import re
import time
from typing import Optional

from . import db

log = logging.getLogger(__name__)


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS alert_rules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        condition_type  TEXT NOT NULL,
        condition_value TEXT NOT NULL,
        channel_ids     TEXT NOT NULL,
        muted           INTEGER DEFAULT 0,
        created_at      INTEGER NOT NULL,
        created_by      TEXT
    )""")


async def create_rule(name: str, condition_type: str, condition_value: str,
                      channel_ids: list, created_by: str = "") -> int:
    """Create a routing rule.

    condition_type: 'severity', 'metric', 'server', 'alert_type', 'pattern'
    condition_value: e.g. 'critical', 'cpu_percent', 'production', '*'
    channel_ids: list of notification channel IDs to route to
    """
    await _ensure_schema()
    now = int(time.time())
    await db.execute(
        """INSERT INTO alert_rules (name, condition_type, condition_value, channel_ids, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, condition_type, condition_value, ",".join(str(c) for c in channel_ids), now, created_by),
    )
    rows = await db.execute("SELECT id FROM alert_rules WHERE name = ? AND created_at = ? ORDER BY id DESC LIMIT 1",
                           (name, now))
    return rows[0]["id"] if rows else 0


async def update_rule(rule_id: int, fields: dict):
    allowed = {"name", "condition_type", "condition_value", "channel_ids", "muted"}
    pairs = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            if k == "channel_ids" and isinstance(v, list):
                v = ",".join(str(c) for c in v)
            pairs.append(f"{k} = ?")
            params.append(v)
    if pairs:
        params.append(rule_id)
        await db.execute(f"UPDATE alert_rules SET {', '.join(pairs)} WHERE id = ?", tuple(params))


async def delete_rule(rule_id: int):
    await db.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))


async def list_rules() -> list:
    await _ensure_schema()
    return await db.execute("SELECT * FROM alert_rules ORDER BY created_at DESC")


async def match_rules(alert: dict) -> list:
    """Given an alert, return list of channel IDs that should receive it.

    If no rules match, returns empty list (use default behavior).
    If rules exist but none match, the alert is still sent to default channels.
    """
    await _ensure_schema()
    rules = await db.execute("SELECT * FROM alert_rules WHERE muted = 0")
    if not rules:
        return []  # No rules = use default (send to all)

    matched_channels = set()
    alert_severity = alert.get("severity", "")
    alert_metric = alert.get("metric_name") or alert.get("metric", "")
    alert_type = alert.get("alert_type", "")
    alert_message = alert.get("message", "")

    for rule in rules:
        ct = rule["condition_type"]
        cv = rule["condition_value"]
        match = False

        if ct == "severity" and cv.lower() == alert_severity.lower():
            match = True
        elif ct == "metric" and cv.lower() in alert_metric.lower():
            match = True
        elif ct == "alert_type" and cv.lower() == alert_type.lower():
            match = True
        elif ct == "server" and cv.lower() in alert_message.lower():
            match = True
        elif ct == "pattern":
            if cv == "*":
                match = True
            else:
                try:
                    if re.search(cv, alert_message, re.IGNORECASE):
                        match = True
                except re.error:
                    if cv.lower() in alert_message.lower():
                        match = True

        if match:
            for cid in rule["channel_ids"].split(","):
                cid = cid.strip()
                if cid:
                    try:
                        matched_channels.add(int(cid))
                    except ValueError:
                        pass

    return list(matched_channels)
