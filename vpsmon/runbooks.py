"""Runbooks — reusable scripts that can be executed on local or remote servers.

- Pre-built library of safe, common runbooks
- User-defined custom runbooks
- Target selection: local, single remote, all servers, tag-matched
- Dry-run preview mode
- Full output + exit code captured & audit-logged
"""
import asyncio
import json
import logging
import shlex
import time
from typing import Optional

from . import db, servers

log = logging.getLogger(__name__)


# --- Built-in runbook library (safe + common ops) ---
BUILTIN_RUNBOOKS = [
    {"slug": "system-info", "name": "System Info", "category": "Diagnostics",
     "description": "Show OS, kernel, uptime, load, memory",
     "script": "echo '=== OS ==='; cat /etc/os-release | head -4; echo; echo '=== Kernel ==='; uname -a; echo; echo '=== Uptime ==='; uptime; echo; echo '=== Memory ==='; free -h; echo; echo '=== Disk ==='; df -h /",
     "dangerous": False},

    {"slug": "disk-usage", "name": "Disk Usage (Top 20 dirs)", "category": "Diagnostics",
     "description": "List top 20 largest directories (depth 3)",
     "script": "du -h --max-depth=3 / 2>/dev/null | sort -h | tail -20",
     "dangerous": False},

    {"slug": "top-processes", "name": "Top 20 Processes by RAM", "category": "Diagnostics",
     "description": "Show processes consuming the most memory",
     "script": "ps aux --sort=-%mem | head -21",
     "dangerous": False},

    {"slug": "network-listeners", "name": "Network Listeners", "category": "Diagnostics",
     "description": "List all listening TCP/UDP ports with owning process",
     "script": "ss -tlnup 2>&1 | head -60",
     "dangerous": False},

    {"slug": "docker-summary", "name": "Docker Summary", "category": "Docker",
     "description": "Running containers, images, volumes, disk usage",
     "script": "echo '=== Containers ==='; docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' 2>&1; echo; echo '=== Images ==='; docker images --format 'table {{.Repository}}:{{.Tag}}\\t{{.Size}}' 2>&1 | head -15; echo; echo '=== Disk ==='; docker system df 2>&1",
     "dangerous": False},

    {"slug": "docker-prune", "name": "Docker Prune (safe)", "category": "Docker",
     "description": "Reclaim space by removing stopped containers and dangling images",
     "script": "docker container prune -f 2>&1; docker image prune -f 2>&1",
     "dangerous": True},

    {"slug": "clear-tmp", "name": "Clear /tmp (older than 7 days)", "category": "Housekeeping",
     "description": "Remove files in /tmp not accessed in last 7 days",
     "script": "BEFORE=$(du -sh /tmp 2>/dev/null | cut -f1); find /tmp -type f -atime +7 -delete 2>/dev/null; AFTER=$(du -sh /tmp 2>/dev/null | cut -f1); echo \"Before: $BEFORE → After: $AFTER\"",
     "dangerous": True},

    {"slug": "clear-journal", "name": "Clear Systemd Journal (keep 2d)", "category": "Housekeeping",
     "description": "Vacuum journalctl logs older than 2 days",
     "script": "journalctl --vacuum-time=2d 2>&1 | tail -5",
     "dangerous": True},

    {"slug": "apt-upgradable", "name": "List Upgradable Packages", "category": "Updates",
     "description": "Show pending apt upgrades + security updates",
     "script": "apt list --upgradable 2>/dev/null | tail -n +2 | head -40; echo; echo '--- Security only ---'; apt list --upgradable 2>/dev/null | grep -i security | wc -l | xargs echo 'Security updates:'",
     "dangerous": False},

    {"slug": "apt-security-upgrade", "name": "Install Security Updates Only", "category": "Updates",
     "description": "Apply only security updates via unattended-upgrade",
     "script": "which unattended-upgrade >/dev/null 2>&1 && unattended-upgrade -d 2>&1 | tail -30 || apt-get -s dist-upgrade 2>&1 | grep -iE 'security|^Inst' | head -30",
     "dangerous": True},

    {"slug": "restart-ssh", "name": "Restart SSH Service", "category": "Services",
     "description": "Restart ssh daemon (keeps current connection!)",
     "script": "systemctl restart ssh 2>&1 || systemctl restart sshd 2>&1; systemctl is-active ssh || systemctl is-active sshd",
     "dangerous": True},

    {"slug": "restart-nginx", "name": "Reload Nginx", "category": "Services",
     "description": "Gracefully reload nginx config (no downtime)",
     "script": "nginx -t 2>&1 && nginx -s reload 2>&1 && echo 'Nginx reloaded OK' || echo 'Nginx config invalid or reload failed'",
     "dangerous": False},

    {"slug": "ssh-authorized-keys", "name": "Audit authorized_keys", "category": "Security",
     "description": "List all SSH keys allowed to log in (root + users)",
     "script": "for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -f \"$f\" ] && echo \"=== $f ===\" && awk '{print NR,$NF,$1,length($0),\"bytes\"}' \"$f\"; done",
     "dangerous": False},

    {"slug": "check-fail2ban", "name": "Fail2ban Status", "category": "Security",
     "description": "Show jails + currently-banned IPs",
     "script": "fail2ban-client status 2>&1; for j in $(fail2ban-client status 2>/dev/null | awk '/Jail list/ {gsub(/,/,\"\");for(i=4;i<=NF;i++)print $i}'); do echo; fail2ban-client status \"$j\" 2>&1; done",
     "dangerous": False},

    {"slug": "last-logins", "name": "Last 20 Logins", "category": "Security",
     "description": "Recent successful SSH logins",
     "script": "last -n 20 -F 2>&1",
     "dangerous": False},

    {"slug": "failed-logins", "name": "Recent Failed Logins", "category": "Security",
     "description": "Failed SSH authentication attempts from journalctl",
     "script": "journalctl _COMM=sshd --since '24 hours ago' 2>/dev/null | grep -i 'failed password\\|invalid user' | tail -30",
     "dangerous": False},

    {"slug": "cron-summary", "name": "Cron Summary", "category": "Scheduling",
     "description": "All cron entries on the system",
     "script": "echo '=== root crontab ==='; crontab -l 2>/dev/null; echo; echo '=== /etc/cron.d ==='; ls -la /etc/cron.d/ 2>/dev/null; echo; echo '=== User crontabs ==='; for u in $(cut -d: -f1 /etc/passwd); do c=$(crontab -u $u -l 2>/dev/null); [ -n \"$c\" ] && echo \"# $u:\" && echo \"$c\"; done; echo; echo '=== Systemd timers ==='; systemctl list-timers --all 2>&1 | head -20",
     "dangerous": False},

    {"slug": "ssl-cert-expiry", "name": "SSL Cert Expiry (Let's Encrypt)", "category": "Security",
     "description": "Show expiry for all LE certificates",
     "script": "for c in /etc/letsencrypt/live/*/cert.pem; do [ -f \"$c\" ] && echo \"$(basename $(dirname $c)): $(openssl x509 -in $c -noout -enddate | cut -d= -f2)\"; done",
     "dangerous": False},
]


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS runbooks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        slug        TEXT UNIQUE NOT NULL,
        name        TEXT NOT NULL,
        category    TEXT DEFAULT 'Custom',
        description TEXT,
        script      TEXT NOT NULL,
        dangerous   INTEGER DEFAULT 0,
        builtin     INTEGER DEFAULT 0,
        created_at  INTEGER NOT NULL,
        created_by  TEXT
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS runbook_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        runbook_id   INTEGER,
        slug         TEXT,
        target       TEXT NOT NULL,
        server_id    INTEGER,
        started_at   INTEGER NOT NULL,
        ended_at     INTEGER,
        exit_code    INTEGER,
        output       TEXT,
        triggered_by TEXT,
        dry_run      INTEGER DEFAULT 0
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_runbook_runs_ts ON runbook_runs(started_at DESC)")

    # Populate built-ins (idempotent)
    for b in BUILTIN_RUNBOOKS:
        exists = await db.execute("SELECT id FROM runbooks WHERE slug = ?", (b["slug"],))
        if not exists:
            await db.execute(
                "INSERT INTO runbooks (slug, name, category, description, script, dangerous, builtin, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'system')",
                (b["slug"], b["name"], b["category"], b["description"], b["script"],
                 1 if b["dangerous"] else 0, int(time.time())),
            )


async def list_runbooks() -> list:
    await _ensure_schema()
    return await db.execute("SELECT * FROM runbooks ORDER BY builtin DESC, category, name")


async def get_runbook(rid: int) -> Optional[dict]:
    rows = await db.execute("SELECT * FROM runbooks WHERE id = ?", (rid,))
    return rows[0] if rows else None


async def get_runbook_by_slug(slug: str) -> Optional[dict]:
    rows = await db.execute("SELECT * FROM runbooks WHERE slug = ?", (slug,))
    return rows[0] if rows else None


async def create_custom(slug: str, name: str, category: str, description: str,
                        script: str, dangerous: bool, created_by: str = "") -> int:
    await _ensure_schema()
    slug = slug.strip().lower().replace(" ", "-")[:64] or f"custom-{int(time.time())}"
    await db.execute(
        "INSERT INTO runbooks (slug, name, category, description, script, dangerous, builtin, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (slug, name, category or "Custom", description or "", script,
         1 if dangerous else 0, int(time.time()), created_by),
    )
    rows = await db.execute("SELECT id FROM runbooks WHERE slug = ?", (slug,))
    return rows[0]["id"] if rows else 0


async def update_custom(rid: int, fields: dict):
    allowed = {"name", "category", "description", "script", "dangerous"}
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    sets = ", ".join(f"{k} = ?" for k, _ in pairs)
    vals = [v for _, v in pairs] + [rid]
    await db.execute(f"UPDATE runbooks SET {sets} WHERE id = ? AND builtin = 0", tuple(vals))


async def delete_custom(rid: int):
    await db.execute("DELETE FROM runbooks WHERE id = ? AND builtin = 0", (rid,))


async def _execute_local(script: str, timeout: int = 120) -> dict:
    proc = await asyncio.create_subprocess_shell(
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {"exit_code": proc.returncode, "output": stdout.decode("utf-8", errors="replace")}
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"exit_code": 124, "output": f"[timeout after {timeout}s]"}


async def _execute_remote(srv: dict, script: str, timeout: int = 120) -> dict:
    """Execute script on a remote server via SSH (same mechanism as probes)."""
    host = srv["hostname"]
    user = srv.get("ssh_user") or "root"
    port = int(srv.get("ssh_port") or 22)
    key_path = srv.get("ssh_key_path") or ""
    pw = srv.get("ssh_password") or ""
    base_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=8",
        "-p", str(port),
    ]
    if key_path:
        base_opts += ["-i", key_path, "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no"]
    if pw and not key_path:
        cmd = ["sshpass", "-p", pw, "ssh"] + base_opts + [f"{user}@{host}", script]
    else:
        cmd = ["ssh"] + base_opts + [f"{user}@{host}", script]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {"exit_code": proc.returncode, "output": stdout.decode("utf-8", errors="replace")}
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"exit_code": 124, "output": f"[timeout after {timeout}s]"}


async def execute(runbook_id: int, target: str, triggered_by: str = "",
                  dry_run: bool = False, timeout: int = 120) -> dict:
    """Execute a runbook.

    target values:
      - 'local'                 → run on the monitoring host itself
      - 'server:{id}'           → run on a specific remote server
      - 'all'                   → run on all enabled remote servers (in parallel)
      - 'tag:{tag}'             → run on all servers whose tags CSV contains {tag}
    """
    rb = await get_runbook(runbook_id)
    if not rb:
        return {"ok": False, "error": "runbook not found"}
    script = rb["script"]
    now = int(time.time())

    if dry_run:
        run_id = await _log_run(rb, target, None, now, now, 0,
                                f"[DRY RUN] Would execute:\n{script}",
                                triggered_by, dry_run=True)
        return {"ok": True, "dry_run": True, "script": script, "run_id": run_id}

    # Build target list
    targets = []  # [(label, executor_fn)]
    if target == "local":
        targets.append(("local", None))
    elif target.startswith("server:"):
        sid = int(target.split(":", 1)[1])
        srv = await servers.get_server(sid)
        if not srv:
            return {"ok": False, "error": f"server {sid} not found"}
        targets.append((f"server:{srv['name']}", srv))
    elif target == "all":
        for srv in await servers.list_servers():
            if srv.get("enabled", 1):
                targets.append((f"server:{srv['name']}", srv))
    elif target.startswith("tag:"):
        tag = target.split(":", 1)[1].strip().lower()
        for srv in await servers.list_servers():
            if not srv.get("enabled", 1):
                continue
            tags = [t.strip().lower() for t in (srv.get("tags") or "").split(",")]
            if tag in tags:
                targets.append((f"server:{srv['name']}", srv))
    else:
        return {"ok": False, "error": f"unknown target '{target}'"}

    if not targets:
        return {"ok": False, "error": "no matching targets"}

    async def _run_one(label, ctx):
        start = int(time.time())
        if ctx is None:
            result = await _execute_local(script, timeout)
        else:
            result = await _execute_remote(ctx, script, timeout)
        end = int(time.time())
        sid = ctx["id"] if ctx else None
        run_id = await _log_run(rb, label, sid, start, end,
                                result["exit_code"], result["output"],
                                triggered_by, dry_run=False)
        return {"label": label, "exit_code": result["exit_code"],
                "output": result["output"], "run_id": run_id,
                "duration_sec": end - start}

    results = await asyncio.gather(*[_run_one(l, c) for l, c in targets])
    return {"ok": True, "dry_run": False, "results": results,
            "total": len(results),
            "succeeded": sum(1 for r in results if r["exit_code"] == 0),
            "failed": sum(1 for r in results if r["exit_code"] != 0)}


async def _log_run(rb: dict, target: str, server_id: Optional[int],
                   started: int, ended: int, exit_code: int,
                   output: str, triggered_by: str, dry_run: bool) -> int:
    output = (output or "")[:50000]  # cap at 50KB per run
    await db.execute(
        """INSERT INTO runbook_runs
        (runbook_id, slug, target, server_id, started_at, ended_at, exit_code, output, triggered_by, dry_run)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rb["id"], rb["slug"], target, server_id, started, ended,
         exit_code, output, triggered_by, 1 if dry_run else 0),
    )
    rows = await db.execute("SELECT id FROM runbook_runs WHERE started_at = ? AND slug = ? ORDER BY id DESC LIMIT 1",
                           (started, rb["slug"]))
    return rows[0]["id"] if rows else 0


async def recent_runs(limit: int = 100) -> list:
    await _ensure_schema()
    return await db.execute(
        "SELECT id, runbook_id, slug, target, server_id, started_at, ended_at, exit_code, triggered_by, dry_run "
        "FROM runbook_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )


async def get_run(run_id: int) -> Optional[dict]:
    rows = await db.execute("SELECT * FROM runbook_runs WHERE id = ?", (run_id,))
    return rows[0] if rows else None
