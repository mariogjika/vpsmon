import asyncio
import logging
import time

from . import config, db
from .collectors import system, docker_mon
from .utils import RingBuffer

log = logging.getLogger(__name__)

_recent_alerts = RingBuffer(maxsize=200)
_ws_subscribers: list[asyncio.Queue] = []
_task: asyncio.Task | None = None


def subscribe(queue: asyncio.Queue):
    _ws_subscribers.append(queue)


def unsubscribe(queue: asyncio.Queue):
    try:
        _ws_subscribers.remove(queue)
    except ValueError:
        pass


async def _notify_subscribers(alert: dict):
    """Push a new alert to all WebSocket subscribers."""
    for q in list(_ws_subscribers):
        try:
            q.put_nowait(alert)
        except asyncio.QueueFull:
            pass


async def _insert_alert(
    alert_type: str,
    severity: str,
    metric_name: str,
    threshold: float | None,
    current_value: float | None,
    message: str,
) -> dict:
    """Insert an alert into the database and notify subscribers."""
    now = int(time.time())
    rows = await db.execute(
        "INSERT INTO alerts (ts, alert_type, severity, metric_name, threshold, "
        "current_value, message) VALUES (?,?,?,?,?,?,?)",
        (now, alert_type, severity, metric_name, threshold, current_value, message),
    )
    # Fetch the inserted alert
    inserted = await db.execute(
        "SELECT * FROM alerts WHERE ts = ? AND alert_type = ? AND metric_name = ? "
        "ORDER BY id DESC LIMIT 1",
        (now, alert_type, metric_name),
    )
    alert = inserted[0] if inserted else {
        "ts": now,
        "alert_type": alert_type,
        "severity": severity,
        "metric_name": metric_name,
        "threshold": threshold,
        "current_value": current_value,
        "message": message,
        "acknowledged": 0,
    }
    _recent_alerts.append(alert)
    await _notify_subscribers(alert)
    return alert


async def _is_duplicate(alert_type: str, metric_name: str, window: int = 300) -> bool:
    """Check if a similar alert was already raised recently (within window seconds)."""
    cutoff = int(time.time()) - window
    rows = await db.execute(
        "SELECT COUNT(*) as cnt FROM alerts "
        "WHERE alert_type = ? AND metric_name = ? AND ts > ? AND acknowledged = 0",
        (alert_type, metric_name, cutoff),
    )
    return rows[0]["cnt"] > 0 if rows else False


async def check_alerts():
    """Run all alert checks against current metrics."""
    sys_data = system.get_latest()
    if not sys_data:
        return

    # CPU check
    cpu = sys_data.get("cpu_percent", 0)
    if cpu > config.ALERT_CPU_THRESHOLD:
        if not await _is_duplicate("threshold", "cpu_percent"):
            await _insert_alert(
                alert_type="threshold",
                severity="critical" if cpu > 95 else "warning",
                metric_name="cpu_percent",
                threshold=config.ALERT_CPU_THRESHOLD,
                current_value=cpu,
                message=f"CPU usage at {cpu:.1f}% (threshold: {config.ALERT_CPU_THRESHOLD}%)",
            )

    # Memory check
    mem = sys_data.get("mem_percent", 0)
    if mem > config.ALERT_MEM_THRESHOLD:
        if not await _is_duplicate("threshold", "mem_percent"):
            await _insert_alert(
                alert_type="threshold",
                severity="critical" if mem > 95 else "warning",
                metric_name="mem_percent",
                threshold=config.ALERT_MEM_THRESHOLD,
                current_value=mem,
                message=f"Memory usage at {mem:.1f}% (threshold: {config.ALERT_MEM_THRESHOLD}%)",
            )

    # Disk check
    disk = sys_data.get("disk_percent", 0)
    if disk > config.ALERT_DISK_THRESHOLD:
        if not await _is_duplicate("threshold", "disk_percent"):
            await _insert_alert(
                alert_type="threshold",
                severity="critical" if disk > 95 else "warning",
                metric_name="disk_percent",
                threshold=config.ALERT_DISK_THRESHOLD,
                current_value=disk,
                message=f"Disk usage at {disk:.1f}% (threshold: {config.ALERT_DISK_THRESHOLD}%)",
            )

    # Load average check (> 2x CPU count)
    cpu_count = sys_data.get("cpu_count", 1)
    load_threshold = cpu_count * 2
    load1 = sys_data.get("load_1", 0)
    if load1 > load_threshold:
        if not await _is_duplicate("threshold", "load_1"):
            await _insert_alert(
                alert_type="threshold",
                severity="warning",
                metric_name="load_1",
                threshold=load_threshold,
                current_value=load1,
                message=f"Load average {load1:.2f} exceeds 2x CPU count ({cpu_count} cores, threshold: {load_threshold})",
            )

    # Swap usage > 50%
    swap_pct = sys_data.get("swap_percent", 0)
    if swap_pct > 50:
        if not await _is_duplicate("threshold", "swap_percent"):
            await _insert_alert(
                alert_type="threshold",
                severity="warning",
                metric_name="swap_percent",
                threshold=50.0,
                current_value=swap_pct,
                message=f"Swap usage at {swap_pct:.1f}% (threshold: 50%)",
            )

    # Docker container in "restarting" state
    if docker_mon.is_available():
        containers = docker_mon.get_latest()
        for c in containers:
            if c.get("state") == "restarting" or c.get("status") == "restarting":
                name = c.get("name", c.get("id", "unknown"))
                if not await _is_duplicate("docker_restart", name):
                    await _insert_alert(
                        alert_type="docker_restart",
                        severity="warning",
                        metric_name=name,
                        threshold=None,
                        current_value=None,
                        message=f"Docker container '{name}' is in restarting state",
                    )


async def get_active_alerts() -> list:
    """Return all unacknowledged alerts, newest first."""
    return await db.execute(
        "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY ts DESC"
    )


async def acknowledge_alert(alert_id: int, username: str = "") -> bool:
    """Acknowledge an alert by ID."""
    now = int(time.time())
    rows = await db.execute(
        "UPDATE alerts SET acknowledged = 1, ack_by = ?, ack_at = ? WHERE id = ? AND acknowledged = 0",
        (username, now, alert_id),
    )
    return True


async def get_alert_history(hours: int = 24) -> list:
    """Return alert history for the given number of hours."""
    cutoff = int(time.time()) - (hours * 3600)
    return await db.execute(
        "SELECT * FROM alerts WHERE ts > ? ORDER BY ts DESC",
        (cutoff,),
    )


def get_recent_alerts(n: int = 50) -> list:
    """Return recent alerts from the ring buffer."""
    return _recent_alerts.get_recent(n)


async def fire_custom_alert(metric: str, value: float = 0, severity: str = "warning",
                            message: str = "", extra: dict = None, alert_type: str = "custom"):
    """Fire a custom alert — used by uptime checks, custom scripts, etc."""
    if await _is_duplicate(alert_type, metric, window=60):
        return None
    return await _insert_alert(
        alert_type=alert_type,
        severity=severity,
        metric_name=metric,
        threshold=None,
        current_value=value,
        message=message or f"{metric} = {value}",
    )


async def _alert_loop():
    """Background loop that periodically checks alert thresholds."""
    while True:
        try:
            await check_alerts()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Alert check error: {e}")
        await asyncio.sleep(config.ALERT_CHECK_INTERVAL)


async def start() -> asyncio.Task:
    """Start the alert checking background task."""
    global _task
    _task = asyncio.create_task(_alert_loop())
    log.info("Alert monitoring started")
    return _task


async def stop():
    """Stop the alert checking background task."""
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    log.info("Alert monitoring stopped")
