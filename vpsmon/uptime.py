"""Synthetic HTTP uptime monitoring.

Watches arbitrary URLs on a schedule, records status + latency, tracks
incidents (down→up transitions), exposes SLA percentages.
"""
import asyncio
import logging
import ssl
import time
from typing import Optional

import aiohttp

from . import db, alerts

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS uptime_checks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        url             TEXT NOT NULL,
        method          TEXT DEFAULT 'GET',
        expect_status   INTEGER DEFAULT 200,
        expect_body     TEXT DEFAULT '',
        interval_sec    INTEGER DEFAULT 60,
        timeout_sec     INTEGER DEFAULT 10,
        headers         TEXT DEFAULT '',
        follow_redirects INTEGER DEFAULT 1,
        enabled         INTEGER DEFAULT 1,
        tags            TEXT DEFAULT '',
        created_at      INTEGER NOT NULL,
        last_checked    INTEGER,
        last_status     INTEGER,
        last_latency_ms INTEGER,
        last_up         INTEGER DEFAULT 1,
        last_error      TEXT
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS uptime_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        check_id   INTEGER NOT NULL,
        ts         INTEGER NOT NULL,
        up         INTEGER NOT NULL,
        status     INTEGER,
        latency_ms INTEGER,
        error      TEXT
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_uptime_history_check_ts ON uptime_history(check_id, ts)")


async def list_checks() -> list:
    return await db.execute("SELECT * FROM uptime_checks ORDER BY name")


async def get_check(cid: int) -> Optional[dict]:
    rows = await db.execute("SELECT * FROM uptime_checks WHERE id = ?", (cid,))
    return rows[0] if rows else None


async def add_check(name: str, url: str, method: str = "GET",
                    expect_status: int = 200, expect_body: str = "",
                    interval_sec: int = 60, timeout_sec: int = 10,
                    headers: str = "", follow_redirects: int = 1,
                    tags: str = "") -> int:
    now = int(time.time())
    await db.execute(
        """INSERT INTO uptime_checks
        (name, url, method, expect_status, expect_body, interval_sec, timeout_sec,
         headers, follow_redirects, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, url, method, expect_status, expect_body, interval_sec,
         timeout_sec, headers, follow_redirects, tags, now),
    )
    rows = await db.execute("SELECT id FROM uptime_checks WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))
    return rows[0]["id"] if rows else 0


async def update_check(cid: int, fields: dict):
    allowed = {"name", "url", "method", "expect_status", "expect_body",
               "interval_sec", "timeout_sec", "headers", "follow_redirects",
               "enabled", "tags"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sets = ", ".join(f"{k} = ?" for k, _ in pairs)
    vals = [v for _, v in pairs] + [cid]
    await db.execute(f"UPDATE uptime_checks SET {sets} WHERE id = ?", tuple(vals))


async def delete_check(cid: int):
    await db.execute("DELETE FROM uptime_checks WHERE id = ?", (cid,))
    await db.execute("DELETE FROM uptime_history WHERE check_id = ?", (cid,))


async def history(cid: int, hours: int = 24) -> list:
    cutoff = int(time.time()) - hours * 3600
    return await db.execute(
        "SELECT ts, up, status, latency_ms, error FROM uptime_history "
        "WHERE check_id = ? AND ts > ? ORDER BY ts ASC",
        (cid, cutoff),
    )


async def sla(cid: int, hours: int = 24) -> dict:
    """Compute uptime percentage over the given period."""
    rows = await history(cid, hours)
    if not rows:
        return {"sla": None, "total_checks": 0, "down_checks": 0, "avg_latency": None}
    total = len(rows)
    down = sum(1 for r in rows if not r["up"])
    latencies = [r["latency_ms"] for r in rows if r["up"] and r["latency_ms"]]
    return {
        "sla": round(100 * (1 - down / total), 3) if total else None,
        "total_checks": total,
        "down_checks": down,
        "avg_latency": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "p95_latency": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 20 else None,
    }


async def _run_check(sess: aiohttp.ClientSession, c: dict) -> dict:
    start = time.monotonic()
    method = (c.get("method") or "GET").upper()
    timeout = aiohttp.ClientTimeout(total=int(c.get("timeout_sec") or 10))
    hdr_lines = (c.get("headers") or "").strip().splitlines()
    headers = {}
    for line in hdr_lines:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip()] = v.strip()
    expect_status = int(c.get("expect_status") or 200)
    expect_body = c.get("expect_body") or ""
    allow_redirects = bool(c.get("follow_redirects"))
    up = True
    status = None
    err = ""
    try:
        async with sess.request(
            method, c["url"],
            headers=headers or None,
            timeout=timeout,
            allow_redirects=allow_redirects,
            ssl=False if c["url"].startswith("https") and "verify=false" in (c.get("tags", "")) else None,
        ) as resp:
            status = resp.status
            text = ""
            if expect_body:
                text = await resp.text()
            if status != expect_status:
                up = False
                err = f"status {status} != {expect_status}"
            elif expect_body and expect_body not in text:
                up = False
                err = f"body missing '{expect_body[:40]}'"
    except asyncio.TimeoutError:
        up = False; err = "timeout"
    except ssl.SSLError as e:
        up = False; err = f"ssl: {e}"
    except aiohttp.ClientError as e:
        up = False; err = f"client: {e}"[:120]
    except Exception as e:
        up = False; err = f"error: {e}"[:120]
    latency_ms = int((time.monotonic() - start) * 1000)
    return {"up": up, "status": status, "latency_ms": latency_ms, "error": err}


async def _persist_result(c: dict, result: dict):
    now = int(time.time())
    await db.execute(
        """UPDATE uptime_checks SET last_checked=?, last_status=?, last_latency_ms=?, last_up=?, last_error=?
           WHERE id = ?""",
        (now, result["status"], result["latency_ms"], 1 if result["up"] else 0,
         result["error"][:255] if result["error"] else "", c["id"]),
    )
    await db.execute(
        """INSERT INTO uptime_history (check_id, ts, up, status, latency_ms, error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (c["id"], now, 1 if result["up"] else 0, result["status"],
         result["latency_ms"], (result["error"] or "")[:255]),
    )

    # Transition detection → fire alert
    was_up = bool(c.get("last_up", 1))
    if was_up and not result["up"]:
        try:
            await alerts.fire_custom_alert(
                metric=f"uptime:{c['name']}",
                value=0,
                severity="critical",
                message=f"{c['name']} is DOWN — {result['error']}",
                extra={"url": c["url"], "status": result["status"]},
            )
        except Exception as e:
            log.warning(f"Uptime alert fire error: {e}")
    elif (not was_up) and result["up"]:
        try:
            await alerts.fire_custom_alert(
                metric=f"uptime:{c['name']}",
                value=1,
                severity="info",
                message=f"{c['name']} recovered — {result['latency_ms']}ms",
                extra={"url": c["url"], "status": result["status"]},
            )
        except Exception as e:
            log.warning(f"Uptime recovery alert error: {e}")


async def prune_history(days: int = 90):
    cutoff = int(time.time()) - days * 86400
    await db.execute("DELETE FROM uptime_history WHERE ts < ?", (cutoff,))


async def _loop():
    """Run all enabled checks in parallel every 15 seconds (checks self-gate by interval)."""
    await _ensure_schema()
    last_run = {}  # check_id -> ts
    last_prune = time.time()
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                checks = await db.execute("SELECT * FROM uptime_checks WHERE enabled = 1")
                now = time.time()
                due = []
                for c in checks:
                    interval = int(c.get("interval_sec") or 60)
                    if now - last_run.get(c["id"], 0) >= interval:
                        due.append(c)
                if due:
                    results = await asyncio.gather(
                        *[_run_check(sess, c) for c in due], return_exceptions=True,
                    )
                    for c, r in zip(due, results):
                        if isinstance(r, Exception):
                            log.warning(f"Uptime check {c['name']} error: {r}")
                            continue
                        await _persist_result(c, r)
                        last_run[c["id"]] = now
                # Prune daily
                if now - last_prune > 86400:
                    await prune_history(90)
                    last_prune = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Uptime loop error: {e}")
            await asyncio.sleep(5)


async def start():
    global _task
    if _task and not _task.done():
        return
    await _ensure_schema()
    _task = asyncio.create_task(_loop())
    log.info("Uptime monitoring started")


async def stop():
    global _task
    if _task:
        _task.cancel()
    log.info("Uptime monitoring stopped")


async def summary() -> dict:
    """Overview for status page."""
    checks = await list_checks()
    total = len(checks)
    up = sum(1 for c in checks if c.get("last_up") and c.get("enabled"))
    down = sum(1 for c in checks if c.get("enabled") and not c.get("last_up"))
    overall_sla = None
    if total:
        all_pct = []
        for c in checks:
            if not c.get("enabled"):
                continue
            s = await sla(c["id"], 24)
            if s["sla"] is not None:
                all_pct.append(s["sla"])
        if all_pct:
            overall_sla = round(sum(all_pct) / len(all_pct), 3)
    return {"total": total, "up": up, "down": down, "sla_24h": overall_sla}
