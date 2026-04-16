"""Prometheus metrics export — /metrics endpoint in Prometheus text format.

Exposes all current system metrics so Grafana users can plug in.
"""
import time

from .collectors import system, docker_mon


def generate_metrics() -> str:
    """Generate Prometheus text exposition format."""
    lines = []

    def _metric(name, value, help_text="", mtype="gauge", labels=None):
        if value is None:
            return
        if help_text:
            lines.append(f"# HELP vpsmon_{name} {help_text}")
        lines.append(f"# TYPE vpsmon_{name} {mtype}")
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
        lines.append(f"vpsmon_{name}{label_str} {value}")

    # System metrics
    try:
        data = system.get_latest()
        if data:
            _metric("cpu_percent", data.get("cpu_percent"), "CPU usage percentage")
            _metric("memory_percent", data.get("memory_percent"), "Memory usage percentage")
            _metric("memory_used_bytes", data.get("memory_used"), "Memory used in bytes")
            _metric("memory_total_bytes", data.get("memory_total"), "Total memory in bytes")
            _metric("swap_percent", data.get("swap_percent"), "Swap usage percentage")
            _metric("disk_percent", data.get("disk_percent"), "Disk usage percentage")
            _metric("disk_used_bytes", data.get("disk_used"), "Disk used in bytes")
            _metric("disk_total_bytes", data.get("disk_total"), "Total disk in bytes")
            _metric("load_1m", data.get("load_1"), "1-minute load average")
            _metric("load_5m", data.get("load_5"), "5-minute load average")
            _metric("load_15m", data.get("load_15"), "15-minute load average")
            _metric("network_bytes_sent", data.get("net_sent"), "Network bytes sent", "counter")
            _metric("network_bytes_recv", data.get("net_recv"), "Network bytes received", "counter")
            _metric("uptime_seconds", data.get("uptime"), "System uptime in seconds")

            # Per-core CPU
            for i, core_pct in enumerate(data.get("cpu_per_core", [])):
                lines.append(f'vpsmon_cpu_core_percent{{core="{i}"}} {core_pct}')
    except Exception:
        pass

    # Docker metrics
    try:
        containers = docker_mon.get_latest()
        if containers:
            running = sum(1 for c in containers if c.get("status") == "running")
            total = len(containers)
            _metric("docker_containers_running", running, "Running Docker containers")
            _metric("docker_containers_total", total, "Total Docker containers")
            for c in containers:
                name = c.get("name", "unknown").replace('"', '\\"')
                if c.get("cpu_percent") is not None:
                    lines.append(f'vpsmon_docker_cpu_percent{{container="{name}"}} {c["cpu_percent"]}')
                if c.get("memory_usage") is not None:
                    lines.append(f'vpsmon_docker_memory_bytes{{container="{name}"}} {c["memory_usage"]}')
    except Exception:
        pass

    _metric("scrape_timestamp", int(time.time()), "Last scrape timestamp")

    return "\n".join(lines) + "\n"
