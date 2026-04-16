import asyncio
import os
import platform
import socket
import sys
import time

import psutil

from ..utils import DeltaCalculator
from .. import config

_delta = DeltaCalculator()
_latest = {}


def get_latest() -> dict:
    return dict(_latest)


async def collect_once() -> dict:
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _collect_sync)
    _latest.update(data)
    return data


def _collect_sync() -> dict:
    now = int(time.time())
    m = {"_ts": now}

    # CPU
    cpu_pcts = psutil.cpu_percent(interval=None, percpu=True)
    m["cpu_percent"] = sum(cpu_pcts) / len(cpu_pcts) if cpu_pcts else 0
    m["cpu_count"] = len(cpu_pcts)
    for i, pct in enumerate(cpu_pcts):
        m[f"cpu_core_{i}"] = pct

    # Load
    load1, load5, load15 = psutil.getloadavg()
    m["load_1"] = load1
    m["load_5"] = load5
    m["load_15"] = load15

    # Memory
    mem = psutil.virtual_memory()
    m["mem_total"] = mem.total
    m["mem_used"] = mem.used
    m["mem_available"] = mem.available
    m["mem_percent"] = mem.percent
    m["mem_cached"] = getattr(mem, "cached", 0)
    m["mem_buffers"] = getattr(mem, "buffers", 0)

    # Swap
    swap = psutil.swap_memory()
    m["swap_total"] = swap.total
    m["swap_used"] = swap.used
    m["swap_percent"] = swap.percent

    # Disk usage
    partitions = psutil.disk_partitions(all=False)
    total_disk = 0
    used_disk = 0
    for p in partitions:
        try:
            usage = psutil.disk_usage(p.mountpoint)
            total_disk += usage.total
            used_disk += usage.used
            safe_mp = p.mountpoint.replace("/", "_").strip("_") or "root"
            m[f"disk_{safe_mp}_total"] = usage.total
            m[f"disk_{safe_mp}_used"] = usage.used
            m[f"disk_{safe_mp}_percent"] = usage.percent
        except (PermissionError, OSError):
            continue
    if total_disk > 0:
        m["disk_percent"] = (used_disk / total_disk) * 100
    else:
        m["disk_percent"] = 0
    m["disk_total"] = total_disk
    m["disk_used"] = used_disk

    # Disk IO
    try:
        dio = psutil.disk_io_counters()
        if dio:
            read_rate = _delta.rate("disk_read", dio.read_bytes)
            write_rate = _delta.rate("disk_write", dio.write_bytes)
            m["disk_read_rate"] = read_rate if read_rate is not None else 0
            m["disk_write_rate"] = write_rate if write_rate is not None else 0
            m["disk_read_total"] = dio.read_bytes
            m["disk_write_total"] = dio.write_bytes
    except Exception:
        m["disk_read_rate"] = 0
        m["disk_write_rate"] = 0

    # Network IO
    try:
        nio = psutil.net_io_counters()
        if nio:
            rx_rate = _delta.rate("net_rx", nio.bytes_recv)
            tx_rate = _delta.rate("net_tx", nio.bytes_sent)
            m["net_rx_rate"] = rx_rate if rx_rate is not None else 0
            m["net_tx_rate"] = tx_rate if tx_rate is not None else 0
            m["net_rx_total"] = nio.bytes_recv
            m["net_tx_total"] = nio.bytes_sent
            m["net_packets_recv"] = nio.packets_recv
            m["net_packets_sent"] = nio.packets_sent
            m["net_errin"] = nio.errin
            m["net_errout"] = nio.errout
    except Exception:
        m["net_rx_rate"] = 0
        m["net_tx_rate"] = 0

    # Misc
    m["boot_time"] = int(psutil.boot_time())
    m["uptime"] = now - int(psutil.boot_time())
    try:
        m["open_files"] = len(psutil.Process().open_files())
    except Exception:
        m["open_files"] = 0
    try:
        m["tcp_connections"] = len([
            c for c in psutil.net_connections(kind="tcp")
            if c.status == "ESTABLISHED"
        ])
    except Exception:
        m["tcp_connections"] = 0

    return m


# Metrics to persist in the time-series DB
PERSIST_METRICS = [
    "cpu_percent", "load_1", "load_5", "load_15",
    "mem_percent", "mem_used", "mem_available",
    "swap_percent", "swap_used",
    "disk_percent", "disk_used",
    "disk_read_rate", "disk_write_rate",
    "net_rx_rate", "net_tx_rate",
    "tcp_connections",
]


def get_persist_rows(data: dict) -> list[tuple]:
    ts = data["_ts"]
    rows = []
    for name in PERSIST_METRICS:
        if name in data:
            rows.append((ts, name, float(data[name]), ""))
    return rows


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _read_cpu_model() -> str:
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.strip().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def get_system_info() -> dict:
    """Return static system information."""
    mem = psutil.virtual_memory()
    disk_total = 0
    for p in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(p.mountpoint)
            disk_total += usage.total
        except (PermissionError, OSError):
            continue

    return {
        "hostname": socket.gethostname(),
        "os_info": platform.platform(),
        "kernel": platform.release(),
        "cpu_model": _read_cpu_model(),
        "cpu_count": psutil.cpu_count(logical=True),
        "total_memory": _format_bytes(mem.total),
        "total_disk": _format_bytes(disk_total),
        "boot_time": int(psutil.boot_time()),
        "python_version": sys.version,
    }
