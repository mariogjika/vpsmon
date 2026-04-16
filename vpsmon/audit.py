"""Audit log — records who did what."""
import time
import logging

from . import db

log = logging.getLogger(__name__)


async def log_event(username: str, action: str, resource: str = "",
                   ip: str = "", details: str = ""):
    """Record an audit event."""
    try:
        await db.execute(
            "INSERT INTO audit_log (ts, username, action, resource, ip, details) VALUES (?,?,?,?,?,?)",
            (int(time.time()), username, action, resource, ip, details)
        )
    except Exception as e:
        log.warning(f"Audit log error: {e}")


async def get_events(hours: int = 24, username: str = None, action: str = None,
                     limit: int = 500) -> list:
    """Query audit events."""
    cutoff = int(time.time()) - (hours * 3600)
    where = ["ts > ?"]
    params = [cutoff]
    if username:
        where.append("username = ?")
        params.append(username)
    if action:
        where.append("action = ?")
        params.append(action)
    params.append(limit)
    sql = f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?"
    return await db.execute(sql, tuple(params))
