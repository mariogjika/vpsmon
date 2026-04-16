"""Agent management — enrollment, heartbeat, metrics ingestion, log shipping.

Supports both Linux and Windows agents pushing data to VPSMon.
Coexists with the existing agentless SSH probing model.
"""
import asyncio
import hashlib
import json
import logging
import secrets
import time
from typing import Optional

from . import db, alerts

log = logging.getLogger(__name__)


# ==================== Schema ====================

async def init_schema():
    """Create agent-related tables."""
    await db.execute("""
    CREATE TABLE IF NOT EXISTS enrollment_tokens (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        token       TEXT UNIQUE NOT NULL,
        name        TEXT,
        created_by  TEXT,
        created_at  INTEGER NOT NULL,
        expires_at  INTEGER,
        max_uses    INTEGER DEFAULT 0,
        use_count   INTEGER DEFAULT 0,
        tags        TEXT DEFAULT '',
        active      INTEGER DEFAULT 1
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS agents (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id        TEXT UNIQUE NOT NULL,
        hostname        TEXT NOT NULL,
        display_name    TEXT,
        os_type         TEXT NOT NULL,
        os_version      TEXT,
        os_distro       TEXT,
        kernel          TEXT,
        arch            TEXT,
        agent_version   TEXT,
        ip_address      TEXT,
        tags            TEXT DEFAULT '',
        enrolled_at     INTEGER NOT NULL,
        enrolled_via    TEXT,
        last_heartbeat  INTEGER,
        heartbeat_interval INTEGER DEFAULT 30,
        status          TEXT DEFAULT 'online',
        config          TEXT DEFAULT '{}',
        server_id       INTEGER,
        FOREIGN KEY (server_id) REFERENCES servers(id)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_heartbeat ON agents(last_heartbeat)")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS agent_metrics (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id    TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        data        TEXT NOT NULL
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_metrics_ts ON agent_metrics(agent_id, ts DESC)")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS agent_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id    TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        source      TEXT,
        severity    TEXT,
        service     TEXT,
        message     TEXT NOT NULL,
        metadata    TEXT
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_logs_ts ON agent_logs(agent_id, ts DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_logs_sev ON agent_logs(severity, ts DESC)")


# ==================== Enrollment Tokens ====================

async def create_enrollment_token(name: str = "", created_by: str = "",
                                   expires_hours: int = 24, max_uses: int = 0,
                                   tags: str = "") -> dict:
    """Create a new enrollment token for agent registration."""
    await init_schema()
    token = "vpsm_enroll_" + secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + expires_hours * 3600 if expires_hours > 0 else None
    await db.execute(
        """INSERT INTO enrollment_tokens (token, name, created_by, created_at, expires_at, max_uses, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token, name, created_by, now, expires_at, max_uses, tags),
    )
    return {"token": token, "expires_at": expires_at, "max_uses": max_uses}


async def validate_enrollment_token(token: str) -> Optional[dict]:
    """Check if an enrollment token is valid and usable."""
    rows = await db.execute(
        "SELECT * FROM enrollment_tokens WHERE token = ? AND active = 1", (token,)
    )
    if not rows:
        return None
    t = rows[0]
    now = int(time.time())
    if t.get("expires_at") and t["expires_at"] < now:
        return None
    if t.get("max_uses") and t["max_uses"] > 0 and t.get("use_count", 0) >= t["max_uses"]:
        return None
    return t


async def consume_enrollment_token(token: str):
    """Increment use count for an enrollment token."""
    await db.execute(
        "UPDATE enrollment_tokens SET use_count = use_count + 1 WHERE token = ?",
        (token,),
    )


async def list_enrollment_tokens() -> list:
    await init_schema()
    rows = await db.execute("SELECT * FROM enrollment_tokens ORDER BY created_at DESC")
    # Mask tokens
    for r in rows:
        r["token_preview"] = r["token"][:20] + "..."
    return rows


async def revoke_enrollment_token(token_id: int):
    await db.execute("UPDATE enrollment_tokens SET active = 0 WHERE id = ?", (token_id,))


# ==================== Agent Registration ====================

async def register_agent(token: str, agent_data: dict) -> Optional[dict]:
    """Register a new agent using an enrollment token.

    agent_data should contain:
        hostname, os_type, os_version, os_distro, kernel, arch,
        agent_version, ip_address, display_name, tags
    """
    await init_schema()
    t = await validate_enrollment_token(token)
    if not t:
        return None

    agent_id = "agent_" + secrets.token_urlsafe(16)
    now = int(time.time())

    await db.execute(
        """INSERT INTO agents (agent_id, hostname, display_name, os_type, os_version,
           os_distro, kernel, arch, agent_version, ip_address, tags,
           enrolled_at, enrolled_via, last_heartbeat, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online')""",
        (
            agent_id,
            agent_data.get("hostname", "unknown"),
            agent_data.get("display_name") or agent_data.get("hostname", "unknown"),
            agent_data.get("os_type", "linux"),
            agent_data.get("os_version", ""),
            agent_data.get("os_distro", ""),
            agent_data.get("kernel", ""),
            agent_data.get("arch", ""),
            agent_data.get("agent_version", ""),
            agent_data.get("ip_address", ""),
            agent_data.get("tags") or t.get("tags", ""),
            now, token[:20] + "...", now,
        ),
    )

    await consume_enrollment_token(token)

    # Generate an agent auth token for subsequent API calls
    agent_token = "vpsm_agent_" + secrets.token_urlsafe(32)
    agent_token_hash = hashlib.sha256(agent_token.encode()).hexdigest()

    # Store agent token (reuse api_tokens table with special scope)
    await db.execute(
        """INSERT INTO api_tokens (username, name, token_hash, prefix, scopes, created_at)
           VALUES (?, ?, ?, ?, 'agent', ?)""",
        (f"agent:{agent_id}", f"Agent {agent_data.get('hostname', '')}", agent_token_hash,
         agent_token[:16] + "...", now),
    )

    log.info(f"Agent registered: {agent_id} ({agent_data.get('hostname')}, {agent_data.get('os_type')})")

    return {
        "agent_id": agent_id,
        "agent_token": agent_token,
        "heartbeat_interval": 30,
        "metrics_interval": 10,
        "log_ship_interval": 30,
    }


# ==================== Agent Auth ====================

async def validate_agent_token(token: str) -> Optional[dict]:
    """Validate an agent auth token. Returns agent info if valid."""
    if not token or not token.startswith("vpsm_agent_"):
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = await db.execute(
        "SELECT username FROM api_tokens WHERE token_hash = ? AND scopes = 'agent'",
        (token_hash,),
    )
    if not rows:
        return None
    agent_id = rows[0]["username"].replace("agent:", "")
    agents = await db.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
    return agents[0] if agents else None


# ==================== Heartbeat ====================

async def heartbeat(agent_id: str, data: dict = None) -> dict:
    """Process a heartbeat from an agent."""
    now = int(time.time())
    await db.execute(
        "UPDATE agents SET last_heartbeat = ?, status = 'online' WHERE agent_id = ?",
        (now, agent_id),
    )
    if data:
        # Update agent metadata if provided
        updates = []
        params = []
        for field in ("agent_version", "ip_address", "os_version", "kernel"):
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])
        if updates:
            params.append(agent_id)
            await db.execute(
                f"UPDATE agents SET {', '.join(updates)} WHERE agent_id = ?",
                tuple(params),
            )
    return {"ok": True, "ts": now, "next_heartbeat": 30}


# ==================== Metrics Ingestion ====================

async def ingest_metrics(agent_id: str, metrics: list) -> dict:
    """Receive pushed metrics from an agent.

    metrics: list of {metric, value, ts?} dicts
    """
    now = int(time.time())
    count = 0
    for m in metrics:
        ts = m.get("ts", now)
        # Store in agent_metrics as JSON
        await db.execute(
            "INSERT INTO agent_metrics (agent_id, ts, data) VALUES (?, ?, ?)",
            (agent_id, ts, json.dumps(m)),
        )
        # Also store in the main metrics_raw table for charting
        try:
            from .storage import metrics as metrics_store
            metric_name = f"agent:{agent_id}:{m.get('metric', 'unknown')}"
            await metrics_store.insert_raw([(metric_name, ts, float(m.get("value", 0)))])
        except Exception:
            pass
        count += 1

    # Update heartbeat
    await db.execute(
        "UPDATE agents SET last_heartbeat = ?, status = 'online' WHERE agent_id = ?",
        (now, agent_id),
    )

    return {"ok": True, "ingested": count}


# ==================== Log Shipping ====================

async def ingest_logs(agent_id: str, logs: list) -> dict:
    """Receive shipped logs from an agent.

    logs: list of {ts, source, severity, service, message, metadata?}
    """
    now = int(time.time())
    count = 0
    for entry in logs:
        await db.execute(
            """INSERT INTO agent_logs (agent_id, ts, source, severity, service, message, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
                entry.get("ts", now),
                entry.get("source", ""),
                entry.get("severity", "info"),
                entry.get("service", ""),
                entry.get("message", "")[:4096],
                json.dumps(entry.get("metadata")) if entry.get("metadata") else None,
            ),
        )
        count += 1

        # Fire alerts for critical/error logs
        sev = entry.get("severity", "").lower()
        if sev in ("critical", "emergency", "alert", "crit"):
            try:
                await alerts.fire_custom_alert(
                    metric=f"agent_log:{agent_id}:{entry.get('service', '')}",
                    value=0,
                    severity="critical",
                    message=f"[{agent_id}] {entry.get('service', '')} — {entry.get('message', '')[:120]}",
                    alert_type="agent-log",
                )
            except Exception:
                pass

    return {"ok": True, "ingested": count}


# ==================== Querying ====================

async def list_agents(status: Optional[str] = None) -> list:
    await init_schema()
    if status:
        rows = await db.execute(
            "SELECT * FROM agents WHERE status = ? ORDER BY display_name",
            (status,),
        )
    else:
        rows = await db.execute("SELECT * FROM agents ORDER BY display_name")

    now = int(time.time())
    for r in rows:
        lb = r.get("last_heartbeat", 0)
        r["seconds_since_heartbeat"] = now - lb if lb else None
        # Mark stale agents
        if lb and (now - lb) > 120:
            r["status"] = "offline"
    return rows


async def get_agent(agent_id: str) -> Optional[dict]:
    rows = await db.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
    return rows[0] if rows else None


async def delete_agent(agent_id: str):
    await db.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    await db.execute("DELETE FROM agent_metrics WHERE agent_id = ?", (agent_id,))
    await db.execute("DELETE FROM agent_logs WHERE agent_id = ?", (agent_id,))
    await db.execute("DELETE FROM api_tokens WHERE username = ?", (f"agent:{agent_id}",))


async def get_agent_metrics(agent_id: str, hours: int = 1) -> list:
    cutoff = int(time.time()) - hours * 3600
    rows = await db.execute(
        "SELECT ts, data FROM agent_metrics WHERE agent_id = ? AND ts > ? ORDER BY ts ASC",
        (agent_id, cutoff),
    )
    for r in rows:
        try:
            r["parsed"] = json.loads(r["data"])
        except Exception:
            r["parsed"] = {}
    return rows


async def search_logs(agent_id: Optional[str] = None, severity: Optional[str] = None,
                      service: Optional[str] = None, query: Optional[str] = None,
                      hours: int = 24, limit: int = 500) -> list:
    """Search agent logs with filters."""
    await init_schema()
    cutoff = int(time.time()) - hours * 3600
    where = ["ts > ?"]
    params = [cutoff]
    if agent_id:
        where.append("agent_id = ?")
        params.append(agent_id)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if service:
        where.append("service = ?")
        params.append(service)
    if query:
        where.append("message LIKE ?")
        params.append(f"%{query}%")
    params.append(limit)
    sql = f"SELECT * FROM agent_logs WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?"
    return await db.execute(sql, tuple(params))


async def log_stats(hours: int = 24) -> dict:
    """Get log statistics for the dashboard."""
    await init_schema()
    cutoff = int(time.time()) - hours * 3600
    rows = await db.execute(
        "SELECT severity, COUNT(*) as cnt FROM agent_logs WHERE ts > ? GROUP BY severity",
        (cutoff,),
    )
    total = sum(r["cnt"] for r in rows)
    by_severity = {r["severity"]: r["cnt"] for r in rows}
    return {"total": total, "by_severity": by_severity, "hours": hours}


# ==================== Stale Agent Detection ====================

_stale_task = None


async def _stale_check_loop():
    """Background loop to mark stale agents offline + alert."""
    while True:
        try:
            now = int(time.time())
            stale_cutoff = now - 120  # 2 minutes without heartbeat
            stale = await db.execute(
                "SELECT agent_id, display_name FROM agents WHERE status = 'online' AND last_heartbeat < ?",
                (stale_cutoff,),
            )
            for agent in stale:
                await db.execute(
                    "UPDATE agents SET status = 'offline' WHERE agent_id = ?",
                    (agent["agent_id"],),
                )
                try:
                    await alerts.fire_custom_alert(
                        metric=f"agent_offline:{agent['agent_id']}",
                        value=0,
                        severity="critical",
                        message=f"Agent {agent['display_name']} went offline (no heartbeat)",
                        alert_type="agent-offline",
                    )
                except Exception:
                    pass

            # Prune old logs (keep 30 days)
            prune_cutoff = now - 30 * 86400
            await db.execute("DELETE FROM agent_logs WHERE ts < ?", (prune_cutoff,))
            # Prune old metrics (keep 7 days)
            metrics_cutoff = now - 7 * 86400
            await db.execute("DELETE FROM agent_metrics WHERE ts < ?", (metrics_cutoff,))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Stale check error: {e}")
        await asyncio.sleep(30)


async def start():
    global _stale_task
    await init_schema()
    _stale_task = asyncio.create_task(_stale_check_loop())
    log.info("Agent management started")


async def stop():
    if _stale_task:
        _stale_task.cancel()
