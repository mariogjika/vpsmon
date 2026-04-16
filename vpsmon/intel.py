"""Intelligence layer — package updates, backup freshness, SSH key audit,
anomaly detection, and custom user-defined metrics.

All share a common storage pattern: server-scoped records in SQLite, async
collectors that gather via SSH (for remote) or subprocess (for local).
"""
import asyncio
import json
import logging
import re
import statistics
import time
from typing import Optional

from . import db, servers, alerts

log = logging.getLogger(__name__)


# ==================== Common Storage ====================

async def _ensure_schema():
    # Package updates per server
    await db.execute("""
    CREATE TABLE IF NOT EXISTS package_updates (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id      INTEGER,
        ts             INTEGER NOT NULL,
        total_updates  INTEGER DEFAULT 0,
        security_updates INTEGER DEFAULT 0,
        packages       TEXT,
        distro         TEXT,
        UNIQUE(server_id)
    )""")

    # Backup inventory
    await db.execute("""
    CREATE TABLE IF NOT EXISTS backup_inventory (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id   INTEGER,
        name        TEXT NOT NULL,
        tool        TEXT,
        path        TEXT,
        last_backup INTEGER,
        size_bytes  INTEGER,
        status      TEXT,
        details     TEXT,
        updated_at  INTEGER NOT NULL
    )""")

    # SSH keys inventory
    await db.execute("""
    CREATE TABLE IF NOT EXISTS ssh_keys (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id    INTEGER,
        user         TEXT NOT NULL,
        key_type     TEXT,
        fingerprint  TEXT,
        comment      TEXT,
        options      TEXT,
        first_seen   INTEGER NOT NULL,
        last_seen    INTEGER NOT NULL,
        UNIQUE(server_id, user, fingerprint)
    )""")

    # Custom metric definitions
    await db.execute("""
    CREATE TABLE IF NOT EXISTS custom_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT UNIQUE NOT NULL,
        label        TEXT,
        script       TEXT NOT NULL,
        target       TEXT DEFAULT 'local',
        interval_sec INTEGER DEFAULT 300,
        unit         TEXT DEFAULT '',
        enabled      INTEGER DEFAULT 1,
        last_ts      INTEGER,
        last_value   REAL,
        last_error   TEXT,
        created_at   INTEGER NOT NULL
    )""")

    # Anomaly baseline cache (per metric)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS anomaly_baseline (
        metric       TEXT PRIMARY KEY,
        window       INTEGER NOT NULL,
        mean         REAL,
        stdev        REAL,
        sample_count INTEGER,
        updated_at   INTEGER
    )""")


# ==================== Package Updates ====================

UPDATE_PROBE_SCRIPT = r"""
DISTRO=$(. /etc/os-release 2>/dev/null && echo "$ID")
SEC=0; TOTAL=0; PKGS="[]"
if command -v apt-get >/dev/null 2>&1; then
    TOTAL=$(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l)
    SEC=$(apt list --upgradable 2>/dev/null | grep -Ei 'security|-security' | wc -l)
    PKGS=$(apt list --upgradable 2>/dev/null | tail -n +2 | head -20 | awk -F/ '{print $1}' | python3 -c 'import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))' 2>/dev/null || echo '[]')
elif command -v dnf >/dev/null 2>&1; then
    TOTAL=$(dnf check-update -q 2>/dev/null | grep -c '^[a-zA-Z]')
    SEC=$(dnf updateinfo list security -q 2>/dev/null | grep -c '^')
    PKGS=$(dnf check-update -q 2>/dev/null | head -20 | awk '{print $1}' | python3 -c 'import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))' 2>/dev/null || echo '[]')
elif command -v yum >/dev/null 2>&1; then
    TOTAL=$(yum check-update -q 2>/dev/null | grep -c '^[a-zA-Z]')
fi
echo "{\"distro\":\"$DISTRO\",\"total\":$TOTAL,\"security\":$SEC,\"packages\":$PKGS}"
"""


async def scan_updates_local() -> dict:
    proc = await asyncio.create_subprocess_shell(
        UPDATE_PROBE_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "timeout"}
    text = stdout.decode("utf-8", errors="replace").strip()
    try:
        last_line = [l for l in text.splitlines() if l.strip().startswith("{")][-1]
        return json.loads(last_line)
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw": text[:500]}


async def scan_updates_remote(srv: dict) -> dict:
    try:
        from .runbooks import _execute_remote
        r = await _execute_remote(srv, UPDATE_PROBE_SCRIPT, timeout=60)
        if r["exit_code"] != 0:
            return {"error": f"exit {r['exit_code']}", "raw": r["output"][:300]}
        text = r["output"].strip()
        last_line = [l for l in text.splitlines() if l.strip().startswith("{")][-1]
        return json.loads(last_line)
    except Exception as e:
        return {"error": str(e)[:200]}


async def _store_updates(server_id: Optional[int], data: dict):
    if "error" in data:
        return
    await db.execute(
        """INSERT INTO package_updates (server_id, ts, total_updates, security_updates, packages, distro)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(server_id) DO UPDATE SET
             ts=excluded.ts, total_updates=excluded.total_updates,
             security_updates=excluded.security_updates, packages=excluded.packages, distro=excluded.distro""",
        (server_id, int(time.time()), int(data.get("total", 0)),
         int(data.get("security", 0)), json.dumps(data.get("packages", [])),
         data.get("distro", "")),
    )
    # Fire alert on high security update count
    sec = int(data.get("security", 0))
    if sec >= 10:
        try:
            await alerts.fire_custom_alert(
                metric=f"security_updates:{server_id or 'local'}",
                value=sec, severity="warning" if sec < 20 else "critical",
                message=f"{sec} security updates pending"
                        + (f" on server {server_id}" if server_id else ""),
                alert_type="security-updates",
            )
        except Exception:
            pass


async def scan_all_updates():
    """Scan local + all remote servers."""
    await _ensure_schema()
    # Local
    try:
        data = await scan_updates_local()
        await _store_updates(None, data)
    except Exception as e:
        log.warning(f"Local update scan failed: {e}")
    # Remote
    try:
        all_srv = await servers.list_servers()
    except Exception:
        all_srv = []
    for srv in all_srv:
        if not srv.get("enabled", 1):
            continue
        try:
            data = await scan_updates_remote(srv)
            await _store_updates(srv["id"], data)
        except Exception as e:
            log.warning(f"Update scan {srv['name']} failed: {e}")


async def list_updates() -> list:
    await _ensure_schema()
    rows = await db.execute("SELECT * FROM package_updates ORDER BY security_updates DESC, total_updates DESC")
    for r in rows:
        if r.get("packages"):
            try:
                r["packages_list"] = json.loads(r["packages"])[:20]
            except Exception:
                r["packages_list"] = []
    return rows


# ==================== Backup Monitoring ====================

BACKUP_PROBE_SCRIPT = r"""
python3 -c '
import os, json, time, subprocess, glob

out = []
now = int(time.time())

# Restic snapshots
for env in ["/etc/restic/env", "/root/.restic-env"]:
    if os.path.exists(env):
        try:
            r = subprocess.run(["bash","-c", f"source {env} && restic snapshots --json 2>/dev/null | tail -c 20000"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip().startswith("["):
                snaps = json.loads(r.stdout)
                if snaps:
                    last = snaps[-1]
                    ts = int(time.mktime(time.strptime(last["time"][:19], "%Y-%m-%dT%H:%M:%S")))
                    out.append({"tool":"restic","name":last.get("hostname","restic"),"path":env,
                                "last_backup":ts,"size":0,"status":"ok"})
                    continue
        except Exception: pass

# Borg
for repo in glob.glob("/var/backups/borg*") + glob.glob("/root/borg*"):
    if os.path.isdir(repo):
        try:
            r = subprocess.run(["borg","list","--last","1","--json-lines", repo],
                               capture_output=True, text=True, timeout=15)
            if r.stdout:
                last = json.loads(r.stdout.strip().splitlines()[-1])
                ts = int(time.mktime(time.strptime(last["time"][:19], "%Y-%m-%dT%H:%M:%S")))
                out.append({"tool":"borg","name":last.get("name","borg"),"path":repo,
                            "last_backup":ts,"size":0,"status":"ok"})
        except Exception: pass

# Rsync snapshot directories (common pattern)
for base in ["/var/backups","/backup","/srv/backup","/mnt/backup"]:
    if os.path.isdir(base):
        for sub in sorted(os.listdir(base)):
            full = os.path.join(base, sub)
            if os.path.isdir(full) or sub.endswith((".tar.gz",".tar",".tgz",".sql.gz",".zip")):
                try:
                    st = os.stat(full)
                    out.append({"tool":"file","name":sub,"path":full,
                                "last_backup":int(st.st_mtime),"size":st.st_size,"status":"ok"})
                except Exception: pass

# Docker volume backup cron hints
for f in glob.glob("/etc/cron.d/*"):
    try:
        txt = open(f).read()
        if any(k in txt.lower() for k in ["backup","restic","borg","rsync","pg_dump","mysqldump"]):
            out.append({"tool":"cron","name":os.path.basename(f),"path":f,
                        "last_backup":0,"size":0,"status":"scheduled","details":txt[:200]})
    except Exception: pass

# Cap + sort
out = out[:50]
print(json.dumps(out))
' 2>/dev/null || echo "[]"
"""


async def scan_backups_local() -> list:
    proc = await asyncio.create_subprocess_shell(
        BACKUP_PROBE_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        last_line = stdout.decode("utf-8", "replace").strip().splitlines()[-1]
        return json.loads(last_line)
    except Exception:
        return []


async def scan_backups_remote(srv: dict) -> list:
    try:
        from .runbooks import _execute_remote
        r = await _execute_remote(srv, BACKUP_PROBE_SCRIPT, timeout=60)
        text = r["output"].strip().splitlines()[-1] if r.get("output") else "[]"
        return json.loads(text)
    except Exception:
        return []


async def _store_backups(server_id: Optional[int], items: list):
    now = int(time.time())
    # Clear old entries for this server
    await db.execute("DELETE FROM backup_inventory WHERE server_id IS ?", (server_id,))
    for it in items:
        await db.execute(
            """INSERT INTO backup_inventory
            (server_id, name, tool, path, last_backup, size_bytes, status, details, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id, it.get("name", "")[:120], it.get("tool", ""),
             it.get("path", "")[:255], int(it.get("last_backup", 0)),
             int(it.get("size", 0)), it.get("status", ""),
             (it.get("details", "") or "")[:500], now),
        )


async def scan_all_backups():
    await _ensure_schema()
    try:
        await _store_backups(None, await scan_backups_local())
    except Exception as e:
        log.warning(f"Local backup scan: {e}")
    try:
        for srv in await servers.list_servers():
            if not srv.get("enabled", 1):
                continue
            try:
                items = await scan_backups_remote(srv)
                await _store_backups(srv["id"], items)
            except Exception as e:
                log.warning(f"Backup scan {srv['name']}: {e}")
    except Exception:
        pass


async def list_backups() -> list:
    await _ensure_schema()
    rows = await db.execute(
        "SELECT * FROM backup_inventory ORDER BY last_backup DESC"
    )
    now = int(time.time())
    for r in rows:
        lb = r.get("last_backup") or 0
        if lb == 0:
            r["age_hours"] = None
            r["health"] = "scheduled" if r.get("status") == "scheduled" else "unknown"
        else:
            age_h = (now - lb) / 3600
            r["age_hours"] = round(age_h, 1)
            r["health"] = "fresh" if age_h < 26 else "warn" if age_h < 48 else "stale"
    return rows


# ==================== SSH Key Inventory ====================

SSH_KEY_PROBE_SCRIPT = r"""
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
    [ -f "$f" ] || continue
    USER=$(basename $(dirname $(dirname "$f")))
    [ "$USER" = "root" ] || USER=$(basename $(dirname $(dirname "$f")))
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        [ "${line:0:1}" = "#" ] && continue
        # Extract options (if any), type, key, comment
        # Compute fingerprint via temp file
        TMP=$(mktemp)
        echo "$line" > "$TMP"
        FP=$(ssh-keygen -l -f "$TMP" 2>/dev/null | awk '{print $2}')
        TYPE=$(echo "$line" | awk '{for(i=1;i<=NF;i++)if($i~/^(ssh-|ecdsa-|sk-)/){print $i;exit}}')
        COMMENT=$(echo "$line" | awk '{n=NF; print $n}' | grep -E '^[^-]' || echo "")
        rm -f "$TMP"
        echo "$USER|$TYPE|$FP|$COMMENT"
    done < "$f"
done
"""


async def _parse_keys(output: str, server_id: Optional[int]) -> list:
    items = []
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[2]:
            user, key_type, fp, comment = parts
            items.append({
                "user": user.strip()[:80],
                "key_type": key_type.strip()[:40],
                "fingerprint": fp.strip()[:100],
                "comment": comment.strip()[:255],
            })
    return items


async def scan_ssh_keys_local() -> list:
    proc = await asyncio.create_subprocess_shell(
        SSH_KEY_PROBE_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return await _parse_keys(stdout.decode("utf-8", "replace"), None)
    except Exception:
        return []


async def scan_ssh_keys_remote(srv: dict) -> list:
    try:
        from .runbooks import _execute_remote
        r = await _execute_remote(srv, SSH_KEY_PROBE_SCRIPT, timeout=30)
        return await _parse_keys(r.get("output", ""), srv["id"])
    except Exception:
        return []


async def _store_keys(server_id: Optional[int], keys: list):
    now = int(time.time())
    # Seen fingerprints this scan
    seen = set()
    for k in keys:
        seen.add(k["fingerprint"])
        # Upsert
        existing = await db.execute(
            "SELECT id FROM ssh_keys WHERE server_id IS ? AND user = ? AND fingerprint = ?",
            (server_id, k["user"], k["fingerprint"]),
        )
        if existing:
            await db.execute(
                "UPDATE ssh_keys SET last_seen = ?, key_type = ?, comment = ? WHERE id = ?",
                (now, k["key_type"], k["comment"], existing[0]["id"]),
            )
        else:
            await db.execute(
                "INSERT INTO ssh_keys (server_id, user, key_type, fingerprint, comment, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (server_id, k["user"], k["key_type"], k["fingerprint"], k["comment"], now, now),
            )


async def scan_all_ssh_keys():
    await _ensure_schema()
    try:
        await _store_keys(None, await scan_ssh_keys_local())
    except Exception as e:
        log.warning(f"Local key scan: {e}")
    try:
        for srv in await servers.list_servers():
            if not srv.get("enabled", 1):
                continue
            try:
                keys = await scan_ssh_keys_remote(srv)
                await _store_keys(srv["id"], keys)
            except Exception as e:
                log.warning(f"SSH key scan {srv['name']}: {e}")
    except Exception:
        pass


async def list_ssh_keys() -> list:
    await _ensure_schema()
    return await db.execute("SELECT * FROM ssh_keys ORDER BY last_seen DESC")


# ==================== Anomaly Detection ====================

async def compute_baseline(metric: str, days: int = 14) -> dict:
    """Compute mean + stdev for a metric over the past N days (hourly tier)."""
    cutoff = int(time.time()) - days * 86400
    rows = []
    for table in ("metrics_1h", "metrics_5m", "metrics_1m"):
        try:
            rows = await db.execute(
                f"SELECT value FROM {table} WHERE metric = ? AND ts > ?",
                (metric, cutoff),
            )
            if len(rows) >= 20:
                break
        except Exception:
            continue
    if len(rows) < 20:
        return {"metric": metric, "error": "insufficient data", "samples": len(rows)}
    values = [r["value"] for r in rows]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    result = {
        "metric": metric,
        "mean": round(mean, 3),
        "stdev": round(stdev, 3),
        "samples": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }
    await db.execute(
        """INSERT INTO anomaly_baseline (metric, window, mean, stdev, sample_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(metric) DO UPDATE SET
             mean=excluded.mean, stdev=excluded.stdev,
             sample_count=excluded.sample_count, updated_at=excluded.updated_at""",
        (metric, days, mean, stdev, len(values), int(time.time())),
    )
    return result


async def check_anomaly(metric: str, current_value: float, sigma: float = 3.0) -> dict:
    """Check if current value is anomalous (>N sigma from baseline)."""
    rows = await db.execute("SELECT mean, stdev FROM anomaly_baseline WHERE metric = ?", (metric,))
    if not rows:
        return {"anomaly": False, "reason": "no baseline"}
    b = rows[0]
    mean = b["mean"]; stdev = b["stdev"]
    if stdev < 0.01:
        return {"anomaly": False, "reason": "flat baseline"}
    z = abs(current_value - mean) / stdev
    return {
        "anomaly": z > sigma,
        "z_score": round(z, 2),
        "sigma_threshold": sigma,
        "baseline_mean": mean,
        "baseline_stdev": stdev,
        "direction": "high" if current_value > mean else "low",
    }


async def scan_anomalies() -> list:
    """Check key metrics for anomalies and return flagged ones."""
    await _ensure_schema()
    # Refresh baselines for standard metrics
    key_metrics = ["cpu_percent", "memory_percent", "disk_percent", "load_1", "network_in", "network_out"]
    alerts_fired = []
    for m in key_metrics:
        try:
            await compute_baseline(m, days=14)
            # Get current value
            rows = await db.execute(
                "SELECT value FROM metrics_raw WHERE metric = ? ORDER BY ts DESC LIMIT 1", (m,)
            )
            if not rows:
                continue
            current = rows[0]["value"]
            res = await check_anomaly(m, current, sigma=3.0)
            if res.get("anomaly"):
                alerts_fired.append({"metric": m, "current": current, **res})
                try:
                    await alerts.fire_custom_alert(
                        metric=f"anomaly:{m}",
                        value=current,
                        severity="warning",
                        message=f"{m} anomaly: {current:.1f} ({res['z_score']}σ {res['direction']} from baseline {res['baseline_mean']:.1f})",
                        alert_type="anomaly",
                    )
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"Anomaly check {m}: {e}")
    return alerts_fired


async def list_baselines() -> list:
    await _ensure_schema()
    return await db.execute("SELECT * FROM anomaly_baseline ORDER BY metric")


# ==================== Custom Metrics ====================

async def list_custom_metrics() -> list:
    await _ensure_schema()
    return await db.execute("SELECT * FROM custom_metrics ORDER BY name")


async def add_custom_metric(name: str, label: str, script: str, target: str = "local",
                            interval_sec: int = 300, unit: str = "") -> int:
    await _ensure_schema()
    name = re.sub(r"[^a-z0-9_]", "_", name.lower())[:64]
    await db.execute(
        """INSERT OR REPLACE INTO custom_metrics
        (name, label, script, target, interval_sec, unit, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, label or name, script, target, interval_sec, unit, int(time.time())),
    )
    rows = await db.execute("SELECT id FROM custom_metrics WHERE name = ?", (name,))
    return rows[0]["id"] if rows else 0


async def delete_custom_metric(mid: int):
    await db.execute("DELETE FROM custom_metrics WHERE id = ?", (mid,))


async def run_custom_metric(m: dict) -> dict:
    """Execute a custom metric once; expect stdout to end with a single number."""
    from .runbooks import _execute_local, _execute_remote
    try:
        if m["target"] == "local":
            r = await _execute_local(m["script"], timeout=30)
        elif m["target"].startswith("server:"):
            sid = int(m["target"].split(":", 1)[1])
            srv = await servers.get_server(sid)
            if not srv:
                raise Exception("server not found")
            r = await _execute_remote(srv, m["script"], timeout=30)
        else:
            raise Exception(f"bad target {m['target']}")
        if r["exit_code"] != 0:
            raise Exception(f"exit {r['exit_code']}")
        # Last non-empty line should be a number
        lines = [l.strip() for l in r["output"].splitlines() if l.strip()]
        if not lines:
            raise Exception("no output")
        try:
            value = float(lines[-1])
        except ValueError:
            raise Exception(f"last line not numeric: {lines[-1][:40]}")
        # Store in metrics_raw so it charts like any other metric
        try:
            from .storage import metrics
            await metrics.insert_raw([(m["name"], int(time.time()), value)])
        except Exception:
            pass
        await db.execute(
            "UPDATE custom_metrics SET last_ts = ?, last_value = ?, last_error = '' WHERE id = ?",
            (int(time.time()), value, m["id"]),
        )
        return {"ok": True, "value": value}
    except Exception as e:
        await db.execute(
            "UPDATE custom_metrics SET last_ts = ?, last_error = ? WHERE id = ?",
            (int(time.time()), str(e)[:200], m["id"]),
        )
        return {"ok": False, "error": str(e)}


# ==================== Background Scheduler ====================

_tasks = []


async def _updates_loop():
    """Scan updates every 6 hours."""
    while True:
        try:
            await scan_all_updates()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Updates loop error: {e}")
        await asyncio.sleep(6 * 3600)


async def _backups_loop():
    """Scan backups every 3 hours."""
    while True:
        try:
            await scan_all_backups()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"Backup loop error: {e}")
        await asyncio.sleep(3 * 3600)


async def _ssh_keys_loop():
    """Scan SSH keys every 24 hours."""
    while True:
        try:
            await scan_all_ssh_keys()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"SSH key loop: {e}")
        await asyncio.sleep(24 * 3600)


async def _anomaly_loop():
    """Run anomaly detection every 5 minutes."""
    await asyncio.sleep(60)  # wait for metrics to warm up
    while True:
        try:
            await scan_anomalies()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Anomaly loop: {e}")
        await asyncio.sleep(300)


async def _custom_metrics_loop():
    """Run custom metrics on their individual schedules."""
    last_run = {}
    while True:
        try:
            metrics_list = await list_custom_metrics()
            now = time.time()
            due = [m for m in metrics_list
                   if m.get("enabled")
                   and now - last_run.get(m["id"], 0) >= int(m.get("interval_sec") or 300)]
            for m in due:
                try:
                    await run_custom_metric(m)
                    last_run[m["id"]] = now
                except Exception as e:
                    log.warning(f"Custom metric {m['name']}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Custom metrics loop: {e}")
        await asyncio.sleep(15)


async def start():
    await _ensure_schema()
    _tasks.append(asyncio.create_task(_updates_loop()))
    _tasks.append(asyncio.create_task(_backups_loop()))
    _tasks.append(asyncio.create_task(_ssh_keys_loop()))
    _tasks.append(asyncio.create_task(_anomaly_loop()))
    _tasks.append(asyncio.create_task(_custom_metrics_loop()))
    log.info("Intel layer started (updates, backups, ssh-keys, anomaly, custom-metrics)")


async def stop():
    for t in _tasks:
        t.cancel()
