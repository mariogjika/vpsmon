"""Windows-specific monitoring — IIS, RDP, Windows Update, Task Scheduler."""
import asyncio
import json
import logging
import time
from typing import Optional

from . import db, servers

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

IIS_PROBE = r"""
powershell -Command "
$result = @{}
# IIS Sites
try {
    Import-Module WebAdministration -ErrorAction SilentlyContinue
    $sites = Get-ChildItem IIS:\Sites -ErrorAction SilentlyContinue | Select-Object Name, State, Bindings, PhysicalPath
    $result['iis_sites'] = @($sites | ForEach-Object { @{name=$_.Name; state=$_.State; bindings=$_.Bindings.Collection.bindingInformation -join ','; path=$_.PhysicalPath} })
    $pools = Get-ChildItem IIS:\AppPools -ErrorAction SilentlyContinue | Select-Object Name, State, ManagedRuntimeVersion
    $result['app_pools'] = @($pools | ForEach-Object { @{name=$_.Name; state=$_.State; runtime=$_.ManagedRuntimeVersion} })
    $result['iis_available'] = $true
} catch { $result['iis_available'] = $false }
$result | ConvertTo-Json -Compress -Depth 3
" 2>$null || echo '{"iis_available":false}'
"""

RDP_PROBE = r"""
powershell -Command "
$result = @{}
# RDP port check
$tcp = Test-NetConnection -ComputerName localhost -Port 3389 -ErrorAction SilentlyContinue -InformationLevel Quiet
$result['rdp_port_open'] = [bool]$tcp
# Terminal Services
$svc = Get-Service TermService -ErrorAction SilentlyContinue
$result['rdp_service'] = if ($svc) { $svc.Status.ToString() } else { 'not found' }
# Active RDP sessions
try {
    $sessions = query session 2>$null | Where-Object { $_ -match 'Active' }
    $result['active_sessions'] = @($sessions).Count
} catch { $result['active_sessions'] = 0 }
$result | ConvertTo-Json -Compress
" 2>$null || echo '{"rdp_port_open":false}'
"""

WINUPDATE_PROBE = r"""
powershell -Command "
$result = @{}
# Pending updates
try {
    $session = New-Object -ComObject Microsoft.Update.Session -ErrorAction SilentlyContinue
    $searcher = $session.CreateUpdateSearcher()
    $pending = $searcher.Search('IsInstalled=0').Updates
    $result['pending_count'] = $pending.Count
    $critical = @($pending | Where-Object { $_.MsrcSeverity -eq 'Critical' }).Count
    $result['critical_count'] = $critical
} catch {
    $result['pending_count'] = -1
    $result['critical_count'] = -1
}
# Last install date
try {
    $last = Get-HotFix -ErrorAction SilentlyContinue | Sort-Object InstalledOn -Descending | Select-Object -First 1
    $result['last_update'] = if ($last.InstalledOn) { $last.InstalledOn.ToString('yyyy-MM-dd') } else { '' }
    $result['last_hotfix'] = $last.HotFixID
} catch { $result['last_update'] = '' }
# Reboot pending
$reboot = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$result['reboot_pending'] = $reboot
$result | ConvertTo-Json -Compress
" 2>$null || echo '{"pending_count":-1}'
"""

TASKSCHEDULER_PROBE = r"""
powershell -Command "
$result = @{}
try {
    $tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.State -ne 'Disabled' }
    $info = $tasks | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
    $failed = @($info | Where-Object { $_.LastTaskResult -ne 0 -and $_.LastRunTime -gt (Get-Date).AddDays(-1) })
    $result['total_tasks'] = @($tasks).Count
    $result['failed_24h'] = $failed.Count
    $result['failures'] = @($failed | Select-Object -First 10 | ForEach-Object {
        @{name=$_.TaskName; result=$_.LastTaskResult; last_run=$_.LastRunTime.ToString('yyyy-MM-dd HH:mm')}
    })
} catch {
    $result['total_tasks'] = -1
    $result['failed_24h'] = -1
}
$result | ConvertTo-Json -Compress -Depth 3
" 2>$null || echo '{"total_tasks":-1}'
"""


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS windows_checks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id   INTEGER,
        ts          INTEGER NOT NULL,
        check_type  TEXT NOT NULL,
        data        TEXT NOT NULL
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_wincheck ON windows_checks(server_id, check_type, ts DESC)")


async def _run_probe(server_id: int, script: str) -> dict:
    from .runbooks import _execute_remote
    srv = await servers.get_server(server_id)
    if not srv:
        return {"error": "server not found"}
    r = await _execute_remote(srv, script, timeout=30)
    text = r.get("output", "").strip()
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"error": "no JSON output", "raw": text[:300]}


async def _store(server_id: int, check_type: str, data: dict):
    await _ensure_schema()
    await db.execute(
        "INSERT INTO windows_checks (server_id, ts, check_type, data) VALUES (?,?,?,?)",
        (server_id, int(time.time()), check_type, json.dumps(data)),
    )
    cutoff = int(time.time()) - 7 * 86400
    await db.execute("DELETE FROM windows_checks WHERE ts < ?", (cutoff,))


async def probe_iis(server_id: int) -> dict:
    data = await _run_probe(server_id, IIS_PROBE)
    if "error" not in data:
        await _store(server_id, "iis", data)
    return data


async def probe_rdp(server_id: int) -> dict:
    data = await _run_probe(server_id, RDP_PROBE)
    if "error" not in data:
        await _store(server_id, "rdp", data)
    return data


async def probe_updates(server_id: int) -> dict:
    data = await _run_probe(server_id, WINUPDATE_PROBE)
    if "error" not in data:
        await _store(server_id, "winupdate", data)
    return data


async def probe_scheduler(server_id: int) -> dict:
    data = await _run_probe(server_id, TASKSCHEDULER_PROBE)
    if "error" not in data:
        await _store(server_id, "taskscheduler", data)
    return data


async def probe_windows(server_id: int) -> dict:
    """Run all Windows checks."""
    results = {}
    for name, fn in [("iis", probe_iis), ("rdp", probe_rdp),
                      ("winupdate", probe_updates), ("taskscheduler", probe_scheduler)]:
        try:
            results[name] = await fn(server_id)
        except Exception as e:
            results[name] = {"error": str(e)[:200]}
    return results


async def get_latest(server_id: int, check_type: Optional[str] = None) -> list:
    await _ensure_schema()
    if check_type:
        rows = await db.execute(
            "SELECT * FROM windows_checks WHERE server_id = ? AND check_type = ? ORDER BY ts DESC LIMIT 1",
            (server_id, check_type),
        )
    else:
        rows = await db.execute(
            "SELECT * FROM windows_checks WHERE server_id = ? ORDER BY check_type, ts DESC",
            (server_id,),
        )
    for r in rows:
        try:
            r["parsed"] = json.loads(r["data"])
        except Exception:
            r["parsed"] = {}
    return rows


async def _is_windows_agent(server_id: int) -> bool:
    """Check if a server's agent reports as Windows."""
    rows = await db.execute(
        "SELECT os_type FROM agents WHERE server_id = ? OR display_name IN "
        "(SELECT hostname FROM servers WHERE id = ?)",
        (server_id, server_id),
    )
    if rows:
        return rows[0].get("os_type", "").lower() == "windows"
    return False


async def _loop():
    await _ensure_schema()
    while True:
        try:
            for srv in await servers.list_servers():
                if not srv.get("enabled", 1):
                    continue
                if await _is_windows_agent(srv["id"]):
                    try:
                        await probe_windows(srv["id"])
                    except Exception as e:
                        log.debug(f"Windows check {srv['name']}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Windows checks loop: {e}")
        await asyncio.sleep(300)


async def start():
    global _task
    await _ensure_schema()
    _task = asyncio.create_task(_loop())
    log.info("Windows checks started")


async def stop():
    if _task:
        _task.cancel()
