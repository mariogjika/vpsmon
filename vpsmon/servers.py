"""Multi-server agentless monitoring via SSH.

Each remote server is probed via SSH periodically. A small shell script
runs on the remote host and returns JSON with CPU/mem/disk/load/uptime/etc.
"""
import asyncio
import json
import logging
import time
from pathlib import Path

from . import db, config

log = logging.getLogger(__name__)

# Remote probe script — outputs JSON with key metrics
REMOTE_PROBE = r"""
#!/bin/sh
# Lightweight agentless probe for VPSMon
set -e

hostname=$(hostname 2>/dev/null || echo unknown)
uptime_secs=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0)
load=$(cut -d' ' -f1,2,3 /proc/loadavg 2>/dev/null || echo "0 0 0")

# CPU
cpu_line1=$(head -n1 /proc/stat)
sleep 0.5
cpu_line2=$(head -n1 /proc/stat)

# Memory
mem_total=$(awk '/^MemTotal:/ {print $2*1024}' /proc/meminfo)
mem_avail=$(awk '/^MemAvailable:/ {print $2*1024}' /proc/meminfo)
mem_used=$((mem_total - mem_avail))
[ "$mem_total" -gt 0 ] && mem_pct=$(awk "BEGIN{printf \"%.1f\", ($mem_used / $mem_total) * 100}") || mem_pct=0
swap_total=$(awk '/^SwapTotal:/ {print $2*1024}' /proc/meminfo)
swap_free=$(awk '/^SwapFree:/ {print $2*1024}' /proc/meminfo)
swap_used=$((swap_total - swap_free))
[ "$swap_total" -gt 0 ] && swap_pct=$(awk "BEGIN{printf \"%.1f\", ($swap_used / $swap_total) * 100}") || swap_pct=0

# Disk
disk_info=$(df -B1 / 2>/dev/null | tail -n1)
disk_total=$(echo "$disk_info" | awk '{print $2}')
disk_used=$(echo "$disk_info" | awk '{print $3}')
[ "$disk_total" -gt 0 ] && disk_pct=$(awk "BEGIN{printf \"%.1f\", ($disk_used / $disk_total) * 100}") || disk_pct=0

# CPU percent from /proc/stat delta
cpu_pct=$(awk -v l1="$cpu_line1" -v l2="$cpu_line2" 'BEGIN {
    split(l1, a, " "); split(l2, b, " ");
    idle1=a[5]; idle2=b[5];
    total1=0; for (i=2;i<=11;i++) total1+=a[i];
    total2=0; for (i=2;i<=11;i++) total2+=b[i];
    dt=total2-total1; di=idle2-idle1;
    if (dt>0) printf "%.1f", 100*(dt-di)/dt; else print "0"
}')

# Processes
proc_count=$(ls /proc 2>/dev/null | grep -cE '^[0-9]+$' || echo 0)

# Docker containers if available
docker_running=0
docker_total=0
if command -v docker >/dev/null 2>&1; then
    docker_running=$(docker ps -q 2>/dev/null | wc -l || echo 0)
    docker_total=$(docker ps -aq 2>/dev/null | wc -l || echo 0)
fi

# Output JSON
cat <<EOF
{
  "ok": true,
  "hostname": "$hostname",
  "ts": $(date +%s),
  "uptime": $uptime_secs,
  "cpu_percent": $cpu_pct,
  "mem_total": $mem_total,
  "mem_used": $mem_used,
  "mem_percent": $mem_pct,
  "swap_total": $swap_total,
  "swap_used": $swap_used,
  "swap_percent": $swap_pct,
  "disk_total": $disk_total,
  "disk_used": $disk_used,
  "disk_percent": $disk_pct,
  "load_1": $(echo "$load" | awk '{print $1}'),
  "load_5": $(echo "$load" | awk '{print $2}'),
  "load_15": $(echo "$load" | awk '{print $3}'),
  "process_count": $proc_count,
  "docker_running": $docker_running,
  "docker_total": $docker_total
}
EOF
"""

# In-memory latest data per server
_latest: dict[int, dict] = {}
_status: dict[int, dict] = {}  # id -> {"online": bool, "last_seen": ts, "error": str}


async def list_servers() -> list[dict]:
    """Return all configured servers."""
    return await db.execute("SELECT * FROM servers ORDER BY name")


async def get_server(server_id: int) -> dict | None:
    rows = await db.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
    return rows[0] if rows else None


async def add_server(name: str, hostname: str, ssh_user: str = "root",
                     ssh_port: int = 22, ssh_key_path: str = "",
                     ssh_password: str = "", tags: str = "") -> int:
    """Add a new server to monitor."""
    now = int(time.time())
    await db.execute(
        """INSERT INTO servers (name, hostname, ssh_user, ssh_port, ssh_key_path,
           ssh_password, tags, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (name, hostname, ssh_user, ssh_port, ssh_key_path, ssh_password, tags, now)
    )
    rows = await db.execute("SELECT id FROM servers WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))
    return rows[0]["id"] if rows else 0


async def update_server(server_id: int, **fields) -> bool:
    allowed = {"name", "hostname", "ssh_user", "ssh_port", "ssh_key_path",
               "ssh_password", "tags", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [server_id]
    await db.execute(f"UPDATE servers SET {cols} WHERE id = ?", tuple(vals))
    return True


async def delete_server(server_id: int) -> bool:
    await db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    _latest.pop(server_id, None)
    _status.pop(server_id, None)
    return True


def get_latest(server_id: int) -> dict | None:
    return _latest.get(server_id)


def get_all_latest() -> dict:
    return dict(_latest)


def get_status(server_id: int) -> dict:
    return _status.get(server_id, {"online": False, "last_seen": 0, "error": ""})


def get_all_status() -> dict:
    return dict(_status)


async def probe_server(srv: dict, timeout: float = 15.0) -> dict:
    """SSH into a server and run the probe. Returns the parsed JSON or {"ok": False, "error": ...}."""
    host = srv["hostname"]
    user = srv.get("ssh_user") or "root"
    port = int(srv.get("ssh_port") or 22)
    key_path = srv.get("ssh_key_path") or ""
    password = srv.get("ssh_password") or ""

    # Build SSH command
    ssh_args = [
        "ssh", "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=" + ("no" if password else "yes"),
        "-o", "LogLevel=ERROR",
        "-p", str(port),
    ]
    if key_path:
        ssh_args += ["-i", key_path, "-o", "IdentitiesOnly=yes"]

    target = f"{user}@{host}"
    ssh_args += [target, "sh", "-s"]

    try:
        if password:
            # Use sshpass
            proc = await asyncio.create_subprocess_exec(
                "sshpass", "-p", password, *ssh_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *ssh_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=REMOTE_PROBE.encode()),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"ok": False, "error": "timeout"}

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace")[:200]
            return {"ok": False, "error": f"ssh exit {proc.returncode}: {err}"}

        try:
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            data["ok"] = True
            return data
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"parse: {e}"}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"command not found: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def probe_and_store(srv: dict):
    """Probe a single server and persist metrics to the time-series DB."""
    sid = srv["id"]
    result = await probe_server(srv)

    if result.get("ok"):
        _latest[sid] = result
        _status[sid] = {
            "online": True,
            "last_seen": int(time.time()),
            "error": "",
        }
        # Persist to DB with server_id tag
        ts = int(result.get("ts", time.time()))
        tag = f"server={sid}"
        rows = []
        for metric in ["cpu_percent", "mem_percent", "disk_percent",
                       "swap_percent", "load_1", "load_5", "load_15"]:
            if metric in result:
                try:
                    rows.append((ts, metric, float(result[metric]), tag))
                except (ValueError, TypeError):
                    pass
        if rows:
            try:
                await db.execute_many(
                    "INSERT INTO metrics_raw (ts, name, value, tags) VALUES (?,?,?,?)",
                    rows
                )
            except Exception as e:
                log.warning(f"Server {sid} metric store error: {e}")
    else:
        _status[sid] = {
            "online": False,
            "last_seen": _status.get(sid, {}).get("last_seen", 0),
            "error": result.get("error", "unknown"),
        }


async def probe_all():
    """Probe all enabled servers in parallel."""
    servers = await list_servers()
    enabled = [s for s in servers if s.get("enabled", 1)]
    if not enabled:
        return
    tasks = [probe_and_store(s) for s in enabled]
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_connection(srv: dict) -> dict:
    """Test SSH connection to a server without storing metrics."""
    result = await probe_server(srv, timeout=10.0)
    return result


_running = False
_task: asyncio.Task | None = None


async def _probe_loop():
    """Background loop that polls all servers every 30 seconds."""
    while _running:
        try:
            await probe_all()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Probe loop error: {e}")
        await asyncio.sleep(30)


async def start():
    global _running, _task
    _running = True
    _task = asyncio.create_task(_probe_loop())
    log.info("Multi-server monitor started")


async def stop():
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    log.info("Multi-server monitor stopped")


async def get_overview() -> dict:
    """Return aggregate overview across all servers."""
    servers = await list_servers()
    online = sum(1 for s in servers if _status.get(s["id"], {}).get("online"))
    offline = len(servers) - online

    # Aggregate metrics
    total_cpu, total_mem, total_disk, count = 0.0, 0.0, 0.0, 0
    total_load = 0.0
    for s in servers:
        d = _latest.get(s["id"])
        if d and d.get("ok"):
            total_cpu += d.get("cpu_percent", 0)
            total_mem += d.get("mem_percent", 0)
            total_disk += d.get("disk_percent", 0)
            total_load += d.get("load_1", 0)
            count += 1

    avg_cpu = total_cpu / count if count else 0
    avg_mem = total_mem / count if count else 0
    avg_disk = total_disk / count if count else 0

    return {
        "total": len(servers),
        "online": online,
        "offline": offline,
        "avg_cpu_percent": round(avg_cpu, 1),
        "avg_mem_percent": round(avg_mem, 1),
        "avg_disk_percent": round(avg_disk, 1),
        "total_load": round(total_load, 2),
    }
