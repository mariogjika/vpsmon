"""Cron job monitoring — track whether scheduled jobs actually ran on time."""
import asyncio
import json
import logging
import time
from typing import Optional

from . import db, alerts, servers

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

CRON_DETECT_SCRIPT = r"""
python3 -c "
import subprocess, json, re

crons = []

# Root crontab
try:
    r = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            parts = line.split(None, 5)
            if len(parts) >= 6:
                crons.append({'name': parts[5][:80], 'schedule': ' '.join(parts[:5]), 'source': 'crontab-root'})
except: pass

# /etc/cron.d
import os, glob
for f in sorted(glob.glob('/etc/cron.d/*')):
    try:
        for line in open(f):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('SHELL') and not line.startswith('PATH') and not line.startswith('MAILTO'):
                parts = line.split(None, 6)
                if len(parts) >= 7:
                    crons.append({'name': parts[6][:80], 'schedule': ' '.join(parts[:5]), 'user': parts[5], 'source': os.path.basename(f)})
    except: pass

# Systemd timers
try:
    r = subprocess.run(['systemctl', 'list-timers', '--all', '--no-pager', '--plain'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and '.timer' in line:
            timer_name = [p for p in parts if '.timer' in p]
            if timer_name:
                crons.append({'name': timer_name[0], 'schedule': 'systemd-timer', 'source': 'systemd'})
except: pass

print(json.dumps(crons[:50]))
" 2>/dev/null || echo "[]"
"""


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS cron_jobs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id           INTEGER,
        name                TEXT NOT NULL,
        schedule_expr       TEXT,
        expected_interval   INTEGER DEFAULT 3600,
        source              TEXT,
        last_seen           INTEGER,
        last_exit_code      INTEGER,
        status              TEXT DEFAULT 'unknown',
        enabled             INTEGER DEFAULT 1,
        created_at          INTEGER NOT NULL,
        UNIQUE(server_id, name)
    )""")


async def register_cron(server_id: Optional[int], name: str, schedule_expr: str = "",
                        expected_interval: int = 3600, source: str = "") -> int:
    await _ensure_schema()
    now = int(time.time())
    await db.execute(
        """INSERT OR IGNORE INTO cron_jobs (server_id, name, schedule_expr, expected_interval, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (server_id, name[:120], schedule_expr, expected_interval, source, now),
    )
    rows = await db.execute("SELECT id FROM cron_jobs WHERE server_id IS ? AND name = ?",
                           (server_id, name[:120]))
    return rows[0]["id"] if rows else 0


async def report_run(server_id: Optional[int], name: str, exit_code: int = 0):
    """Mark a cron as having run."""
    now = int(time.time())
    status = "ok" if exit_code == 0 else "failing"
    await db.execute(
        "UPDATE cron_jobs SET last_seen = ?, last_exit_code = ?, status = ? WHERE server_id IS ? AND name = ?",
        (now, exit_code, status, server_id, name),
    )


async def check_missed():
    """Check for crons that haven't run within expected interval."""
    await _ensure_schema()
    now = int(time.time())
    rows = await db.execute(
        "SELECT * FROM cron_jobs WHERE enabled = 1 AND last_seen IS NOT NULL"
    )
    for r in rows:
        expected = int(r.get("expected_interval") or 3600)
        last = r.get("last_seen") or 0
        if last and (now - last) > expected * 1.5:
            if r.get("status") != "missed":
                await db.execute(
                    "UPDATE cron_jobs SET status = 'missed' WHERE id = ?", (r["id"],)
                )
                try:
                    await alerts.fire_custom_alert(
                        metric=f"cron_missed:{r['name']}",
                        value=now - last,
                        severity="warning",
                        message=f"Cron '{r['name']}' missed — last ran {(now-last)//60}m ago (expected every {expected//60}m)",
                        alert_type="cron-missed",
                    )
                except Exception:
                    pass


async def auto_detect(server_id: Optional[int] = None):
    """Detect crons on a server via SSH probe."""
    from .runbooks import _execute_local, _execute_remote
    if server_id is None:
        r = await _execute_local(CRON_DETECT_SCRIPT, timeout=15)
    else:
        srv = await servers.get_server(server_id)
        if not srv:
            return []
        r = await _execute_remote(srv, CRON_DETECT_SCRIPT, timeout=15)

    text = r.get("output", "").strip()
    try:
        last_line = [l for l in text.splitlines() if l.strip().startswith("[")][-1]
        crons = json.loads(last_line)
    except Exception:
        return []

    for c in crons:
        await register_cron(
            server_id=server_id,
            name=c.get("name", "")[:120],
            schedule_expr=c.get("schedule", ""),
            source=c.get("source", ""),
        )
    return crons


async def list_crons(server_id: Optional[int] = None) -> list:
    await _ensure_schema()
    if server_id is not None:
        return await db.execute(
            "SELECT * FROM cron_jobs WHERE server_id IS ? ORDER BY name", (server_id,)
        )
    return await db.execute("SELECT * FROM cron_jobs ORDER BY status DESC, name")


async def delete_cron(cron_id: int):
    await db.execute("DELETE FROM cron_jobs WHERE id = ?", (cron_id,))


async def _loop():
    await _ensure_schema()
    last_detect = 0
    while True:
        try:
            await check_missed()
            # Auto-detect every 6 hours
            if time.time() - last_detect > 6 * 3600:
                try:
                    await auto_detect(None)
                    for srv in await servers.list_servers():
                        if srv.get("enabled", 1):
                            try:
                                await auto_detect(srv["id"])
                            except Exception:
                                pass
                    last_detect = time.time()
                except Exception as e:
                    log.debug(f"Cron auto-detect: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Cron monitor: {e}")
        await asyncio.sleep(60)


async def start():
    global _task
    await _ensure_schema()
    _task = asyncio.create_task(_loop())
    log.info("Cron monitoring started")


async def stop():
    if _task:
        _task.cancel()
