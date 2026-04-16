"""Log pattern detection — auto-detect common security and ops issues.

Runs against shipped agent logs to detect:
- SSH brute force attempts
- Service crash loops
- Disk space warnings
- Kernel errors
- Nginx 5xx spikes
- Docker restart loops
- Failed logins (Linux + Windows)
- Unexpected reboots
"""
import asyncio
import logging
import re
import time
from typing import Optional

from . import db, alerts

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

# Pattern definitions: (name, severity, regex_or_keywords, description)
PATTERNS = [
    {
        "name": "ssh_brute_force",
        "severity": "critical",
        "keywords": ["failed password", "invalid user", "authentication failure"],
        "threshold": 10,  # N matches in window
        "window_minutes": 5,
        "description": "SSH brute force detected — {count} failed attempts in {window}m",
    },
    {
        "name": "service_crash",
        "severity": "warning",
        "keywords": ["segfault", "core dumped", "killed process", "oom-killer"],
        "threshold": 1,
        "window_minutes": 15,
        "description": "Service crash detected: {sample}",
    },
    {
        "name": "disk_full_warning",
        "severity": "warning",
        "keywords": ["no space left on device", "disk full", "filesystem is full"],
        "threshold": 1,
        "window_minutes": 30,
        "description": "Disk space issue detected: {sample}",
    },
    {
        "name": "kernel_error",
        "severity": "warning",
        "keywords": ["kernel: BUG", "kernel panic", "kernel: Call Trace", "hardware error"],
        "threshold": 1,
        "window_minutes": 60,
        "description": "Kernel error detected: {sample}",
    },
    {
        "name": "nginx_5xx_spike",
        "severity": "warning",
        "keywords": ["HTTP/1.1\" 5", "HTTP/2\" 5", "upstream timed out", "502 bad gateway", "503 service"],
        "threshold": 20,
        "window_minutes": 5,
        "description": "Nginx 5xx spike — {count} errors in {window}m",
    },
    {
        "name": "docker_restart_loop",
        "severity": "warning",
        "keywords": ["container restart", "restarting container", "back-off restarting"],
        "threshold": 3,
        "window_minutes": 10,
        "description": "Docker container restart loop detected: {sample}",
    },
    {
        "name": "windows_failed_login",
        "severity": "warning",
        "keywords": ["logon failure", "account failed to log on", "logon type: 10"],
        "threshold": 5,
        "window_minutes": 10,
        "description": "Windows failed login spike — {count} failures in {window}m",
    },
    {
        "name": "unexpected_reboot",
        "severity": "critical",
        "keywords": ["system boot", "reboot", "shutdown", "system halted", "power cycle"],
        "threshold": 1,
        "window_minutes": 60,
        "description": "Unexpected reboot detected: {sample}",
    },
    {
        "name": "certificate_expiry",
        "severity": "warning",
        "keywords": ["certificate has expired", "ssl certificate problem", "cert expired"],
        "threshold": 1,
        "window_minutes": 60,
        "description": "SSL certificate issue: {sample}",
    },
    {
        "name": "memory_pressure",
        "severity": "warning",
        "keywords": ["oom", "out of memory", "memory cgroup", "cannot allocate memory"],
        "threshold": 1,
        "window_minutes": 15,
        "description": "Memory pressure detected: {sample}",
    },
]


async def scan_patterns(hours: int = 1) -> list:
    """Scan agent logs for known patterns and fire alerts."""
    cutoff = int(time.time()) - hours * 3600
    detected = []

    for pattern in PATTERNS:
        # Build query to find matching logs
        conditions = []
        params = [cutoff]
        for kw in pattern["keywords"]:
            conditions.append("LOWER(message) LIKE ?")
            params.append(f"%{kw.lower()}%")

        if not conditions:
            continue

        window_cutoff = int(time.time()) - pattern["window_minutes"] * 60
        sql = f"""SELECT COUNT(*) as cnt, MAX(message) as sample
                  FROM agent_logs
                  WHERE ts > ? AND ({' OR '.join(conditions)})
                  AND ts > {window_cutoff}"""

        try:
            rows = await db.execute(sql, tuple(params))
            if rows and rows[0]["cnt"] >= pattern["threshold"]:
                count = rows[0]["cnt"]
                sample = (rows[0]["sample"] or "")[:120]
                desc = pattern["description"].format(
                    count=count,
                    window=pattern["window_minutes"],
                    sample=sample,
                )
                detected.append({
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "description": desc,
                    "count": count,
                    "window_minutes": pattern["window_minutes"],
                })
                # Fire alert
                try:
                    await alerts.fire_custom_alert(
                        metric=f"log_pattern:{pattern['name']}",
                        value=count,
                        severity=pattern["severity"],
                        message=desc,
                        alert_type="log-pattern",
                    )
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"Pattern scan {pattern['name']}: {e}")

    return detected


async def get_pattern_definitions() -> list:
    """Return all pattern definitions for the UI."""
    return [{
        "name": p["name"],
        "severity": p["severity"],
        "keywords": p["keywords"],
        "threshold": p["threshold"],
        "window_minutes": p["window_minutes"],
        "description": p["description"],
    } for p in PATTERNS]


async def _pattern_loop():
    """Background loop — scan patterns every 2 minutes."""
    await asyncio.sleep(30)  # Wait for initial data
    while True:
        try:
            results = await scan_patterns(hours=1)
            if results:
                log.info(f"Log patterns detected: {len(results)} matches")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Pattern scan error: {e}")
        await asyncio.sleep(120)


async def start():
    global _task
    _task = asyncio.create_task(_pattern_loop())
    log.info("Log pattern detection started")


async def stop():
    if _task:
        _task.cancel()
