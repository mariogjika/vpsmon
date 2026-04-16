#!/usr/bin/env python3
"""VPSMon Agent — lightweight daemon for Linux and Windows.

Collects system metrics, monitors services, ships logs.
Pushes data to VPSMon server over HTTPS.

Usage:
  vpsmon-agent.py --server https://monitoring.example.com --token AGENT_TOKEN
  vpsmon-agent.py --enroll --server URL --enroll-token ENROLL_TOKEN --name my-server
"""
import argparse
import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

__version__ = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] vpsmon-agent: %(message)s",
)
log = logging.getLogger("vpsmon-agent")

# ==================== Config ====================

CONFIG_PATH = "/etc/vpsmon/agent.json" if os.name != "nt" else r"C:\ProgramData\vpsmon\agent.json"
LOG_PATH = "/var/log/vpsmon-agent.log" if os.name != "nt" else r"C:\ProgramData\vpsmon\agent.log"

DEFAULT_CONFIG = {
    "server": "",
    "agent_token": "",
    "agent_id": "",
    "hostname": socket.gethostname(),
    "display_name": "",
    "tags": "",
    "metrics_interval": 10,
    "heartbeat_interval": 30,
    "log_ship_interval": 30,
    "log_sources": [],
    "collect_services": True,
    "collect_docker": True,
    "collect_logs": True,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    # Restrict permissions on Linux
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)


# ==================== API Client ====================

def api_call(server: str, path: str, data: dict = None, token: str = None,
             method: str = "POST", timeout: int = 15) -> dict:
    """Make an API call to the VPSMon server."""
    url = f"{server.rstrip('/')}/api/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json", "User-Agent": f"VPSMon-Agent/{__version__}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        # Skip SSL verification for self-signed certs (configurable later)
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        log.error(f"API error {e.code}: {body[:200]}")
        return {"error": f"HTTP {e.code}", "detail": body[:200]}
    except Exception as e:
        log.error(f"API call failed: {e}")
        return {"error": str(e)}


# ==================== Enrollment ====================

def enroll(server: str, enroll_token: str, name: str = "", tags: str = "") -> dict:
    """Enroll this machine with the VPSMon server."""
    os_type = "windows" if os.name == "nt" else "linux"
    data = {
        "token": enroll_token,
        "hostname": socket.gethostname(),
        "display_name": name or socket.gethostname(),
        "os_type": os_type,
        "os_version": platform.version(),
        "os_distro": _get_distro(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "agent_version": __version__,
        "ip_address": _get_ip(),
        "tags": tags,
    }
    result = api_call(server, "agent/register", data)
    if result.get("agent_id"):
        cfg = load_config()
        cfg["server"] = server
        cfg["agent_token"] = result["agent_token"]
        cfg["agent_id"] = result["agent_id"]
        cfg["display_name"] = name or socket.gethostname()
        cfg["tags"] = tags
        cfg["metrics_interval"] = result.get("metrics_interval", 10)
        cfg["heartbeat_interval"] = result.get("heartbeat_interval", 30)
        save_config(cfg)
        log.info(f"Enrolled successfully as {result['agent_id']}")
        return result
    else:
        log.error(f"Enrollment failed: {result.get('error', 'unknown')}")
        return result


# ==================== Collectors ====================

def _get_distro() -> str:
    if os.name == "nt":
        return f"Windows {platform.version()}"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return platform.system()


def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def collect_linux_metrics() -> list:
    """Collect system metrics on Linux."""
    metrics = []
    ts = int(time.time())

    # CPU
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        total = sum(int(x) for x in parts[1:])
        idle = int(parts[4])
        # Simple CPU percent (needs delta, so first call is approximate)
        if not hasattr(collect_linux_metrics, '_prev_cpu'):
            collect_linux_metrics._prev_cpu = (total, idle)
        prev_total, prev_idle = collect_linux_metrics._prev_cpu
        dt = total - prev_total
        di = idle - prev_idle
        cpu_pct = round((1 - di / max(dt, 1)) * 100, 1) if dt > 0 else 0
        collect_linux_metrics._prev_cpu = (total, idle)
        metrics.append({"metric": "cpu_percent", "value": cpu_pct, "ts": ts})
    except Exception:
        pass

    # Memory
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available
        metrics.append({"metric": "memory_total", "value": total, "ts": ts})
        metrics.append({"metric": "memory_used", "value": used, "ts": ts})
        metrics.append({"metric": "memory_percent", "value": round(used / max(total, 1) * 100, 1), "ts": ts})
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        if swap_total:
            metrics.append({"metric": "swap_percent", "value": round((swap_total - swap_free) / swap_total * 100, 1), "ts": ts})
    except Exception:
        pass

    # Disk
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used = total - free
        metrics.append({"metric": "disk_total", "value": total, "ts": ts})
        metrics.append({"metric": "disk_used", "value": used, "ts": ts})
        metrics.append({"metric": "disk_percent", "value": round(used / max(total, 1) * 100, 1), "ts": ts})
    except Exception:
        pass

    # Load
    try:
        load1, load5, load15 = os.getloadavg()
        metrics.append({"metric": "load_1", "value": round(load1, 2), "ts": ts})
        metrics.append({"metric": "load_5", "value": round(load5, 2), "ts": ts})
        metrics.append({"metric": "load_15", "value": round(load15, 2), "ts": ts})
    except Exception:
        pass

    # Network
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" in line and not line.strip().startswith("lo:"):
                    parts = line.split()
                    iface = parts[0].rstrip(":")
                    rx = int(parts[1])
                    tx = int(parts[9])
                    metrics.append({"metric": f"net_rx_{iface}", "value": rx, "ts": ts})
                    metrics.append({"metric": f"net_tx_{iface}", "value": tx, "ts": ts})
                    break  # Primary interface only
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_sec = int(float(f.readline().split()[0]))
        metrics.append({"metric": "uptime", "value": uptime_sec, "ts": ts})
    except Exception:
        pass

    # Process count
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
        metrics.append({"metric": "process_count", "value": len(pids), "ts": ts})
    except Exception:
        pass

    return metrics


def collect_windows_metrics() -> list:
    """Collect system metrics on Windows (uses wmic/powershell)."""
    metrics = []
    ts = int(time.time())

    try:
        # CPU via powershell
        r = subprocess.run(
            ["powershell", "-Command",
             "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout.strip():
            metrics.append({"metric": "cpu_percent", "value": float(r.stdout.strip()), "ts": ts})
    except Exception:
        pass

    try:
        # Memory via powershell
        r = subprocess.run(
            ["powershell", "-Command",
             "$os = Get-CimInstance Win32_OperatingSystem; "
             "$total = $os.TotalVisibleMemorySize * 1024; "
             "$free = $os.FreePhysicalMemory * 1024; "
             "$used = $total - $free; "
             "\"$total|$used|$free\""],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and "|" in r.stdout:
            parts = r.stdout.strip().split("|")
            total, used, free = int(parts[0]), int(parts[1]), int(parts[2])
            metrics.append({"metric": "memory_total", "value": total, "ts": ts})
            metrics.append({"metric": "memory_used", "value": used, "ts": ts})
            metrics.append({"metric": "memory_percent", "value": round(used / max(total, 1) * 100, 1), "ts": ts})
    except Exception:
        pass

    try:
        # Disk via powershell
        r = subprocess.run(
            ["powershell", "-Command",
             "$d = Get-CimInstance Win32_LogicalDisk -Filter 'DeviceID=\"C:\"'; "
             "\"$($d.Size)|$($d.FreeSpace)\""],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and "|" in r.stdout:
            total, free = r.stdout.strip().split("|")
            total, free = int(total), int(free)
            used = total - free
            metrics.append({"metric": "disk_total", "value": total, "ts": ts})
            metrics.append({"metric": "disk_used", "value": used, "ts": ts})
            metrics.append({"metric": "disk_percent", "value": round(used / max(total, 1) * 100, 1), "ts": ts})
    except Exception:
        pass

    try:
        # Uptime via powershell
        r = subprocess.run(
            ["powershell", "-Command",
             "((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds -as [int]"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            metrics.append({"metric": "uptime", "value": int(r.stdout.strip()), "ts": ts})
    except Exception:
        pass

    return metrics


def collect_services_linux() -> list:
    """Get systemd service statuses."""
    services = []
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--plain", "--all"],
            capture_output=True, text=True, timeout=10
        )
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0].endswith(".service"):
                services.append({
                    "name": parts[0],
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                })
    except Exception:
        pass
    return services[:100]  # Cap


def collect_services_windows() -> list:
    """Get Windows service statuses."""
    services = []
    try:
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-Service | Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                data = [data]
            for svc in data[:200]:
                services.append({
                    "name": svc.get("Name", ""),
                    "display_name": svc.get("DisplayName", ""),
                    "status": str(svc.get("Status", "")),
                    "start_type": str(svc.get("StartType", "")),
                })
    except Exception:
        pass
    return services


def collect_logs_linux(since_ts: int = None) -> list:
    """Collect recent journal logs."""
    logs = []
    try:
        cmd = ["journalctl", "--no-pager", "-o", "json", "-n", "100"]
        if since_ts:
            cmd.extend(["--since", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since_ts))])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines():
            try:
                entry = json.loads(line)
                logs.append({
                    "ts": int(entry.get("__REALTIME_TIMESTAMP", "0")[:10]) or int(time.time()),
                    "source": "journal",
                    "severity": _journal_priority(entry.get("PRIORITY", "6")),
                    "service": entry.get("_SYSTEMD_UNIT", entry.get("SYSLOG_IDENTIFIER", "")),
                    "message": entry.get("MESSAGE", "")[:2048],
                })
            except Exception:
                pass
    except Exception:
        pass
    return logs


def collect_logs_windows(since_ts: int = None) -> list:
    """Collect recent Windows Event Log entries."""
    logs = []
    try:
        for log_name in ["System", "Application", "Security"]:
            r = subprocess.run(
                ["powershell", "-Command",
                 f"Get-EventLog -LogName {log_name} -Newest 30 -ErrorAction SilentlyContinue | "
                 "Select-Object TimeGenerated, EntryType, Source, Message | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                if isinstance(data, dict):
                    data = [data]
                for entry in data:
                    sev = str(entry.get("EntryType", "")).lower()
                    sev_map = {"error": "error", "warning": "warning", "information": "info",
                               "failureaudit": "error", "successaudit": "info"}
                    logs.append({
                        "ts": int(time.time()),
                        "source": f"eventlog:{log_name}",
                        "severity": sev_map.get(sev, "info"),
                        "service": entry.get("Source", ""),
                        "message": str(entry.get("Message", ""))[:2048],
                    })
    except Exception:
        pass
    return logs


def _journal_priority(p) -> str:
    try:
        p = int(p)
    except (ValueError, TypeError):
        return "info"
    return {0: "emergency", 1: "alert", 2: "critical", 3: "error",
            4: "warning", 5: "notice", 6: "info", 7: "debug"}.get(p, "info")


# ==================== Main Loop ====================

_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False
    log.info("Shutting down...")


def main_loop(cfg: dict):
    """Main agent loop — collect and push metrics, heartbeat, logs."""
    global _running
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server = cfg["server"]
    token = cfg["agent_token"]
    is_windows = os.name == "nt"

    last_metrics = 0
    last_heartbeat = 0
    last_logs = 0
    last_log_ts = int(time.time()) - 300  # Start from 5 min ago

    log.info(f"Agent started — server: {server}, id: {cfg['agent_id']}, os: {'windows' if is_windows else 'linux'}")

    while _running:
        now = time.time()

        # Heartbeat
        if now - last_heartbeat >= cfg.get("heartbeat_interval", 30):
            result = api_call(server, "agent/heartbeat", {
                "agent_id": cfg["agent_id"],
                "agent_version": __version__,
                "ip_address": _get_ip(),
            }, token)
            if result.get("error"):
                log.warning(f"Heartbeat failed: {result['error']}")
            last_heartbeat = now

        # Metrics
        if now - last_metrics >= cfg.get("metrics_interval", 10):
            try:
                metrics = collect_windows_metrics() if is_windows else collect_linux_metrics()
                if metrics:
                    result = api_call(server, "agent/metrics", {
                        "agent_id": cfg["agent_id"],
                        "metrics": metrics,
                    }, token)
                    if result.get("error"):
                        log.warning(f"Metrics push failed: {result['error']}")
            except Exception as e:
                log.error(f"Metrics collection error: {e}")
            last_metrics = now

        # Log shipping
        if cfg.get("collect_logs", True) and now - last_logs >= cfg.get("log_ship_interval", 30):
            try:
                logs = collect_logs_windows(last_log_ts) if is_windows else collect_logs_linux(last_log_ts)
                if logs:
                    result = api_call(server, "agent/logs", {
                        "agent_id": cfg["agent_id"],
                        "logs": logs[-200],  # Cap per batch
                    }, token)
                    if result.get("error"):
                        log.warning(f"Log ship failed: {result['error']}")
                last_log_ts = int(now)
            except Exception as e:
                log.error(f"Log shipping error: {e}")
            last_logs = now

        time.sleep(1)

    log.info("Agent stopped")


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="VPSMon Agent")
    parser.add_argument("--server", help="VPSMon server URL")
    parser.add_argument("--token", help="Agent auth token (from enrollment)")
    parser.add_argument("--enroll", action="store_true", help="Enroll this machine")
    parser.add_argument("--enroll-token", help="Enrollment token")
    parser.add_argument("--name", help="Display name for this server")
    parser.add_argument("--tags", help="Comma-separated tags", default="")
    parser.add_argument("--status", action="store_true", help="Show agent status")
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        print(f"VPSMon Agent v{__version__}")
        return

    cfg = load_config()

    if args.status:
        if cfg.get("agent_id"):
            print(f"Agent ID:    {cfg['agent_id']}")
            print(f"Server:      {cfg['server']}")
            print(f"Hostname:    {cfg.get('hostname', socket.gethostname())}")
            print(f"OS:          {'Windows' if os.name == 'nt' else 'Linux'}")
            print(f"Config:      {CONFIG_PATH}")
            print(f"Status:      configured")
        else:
            print("Agent not enrolled. Run with --enroll to register.")
        return

    if args.enroll:
        if not args.server or not args.enroll_token:
            print("Error: --server and --enroll-token required for enrollment")
            sys.exit(1)
        result = enroll(args.server, args.enroll_token, args.name or "", args.tags or "")
        if result.get("agent_id"):
            print(f"Enrolled successfully!")
            print(f"  Agent ID: {result['agent_id']}")
            print(f"  Config:   {CONFIG_PATH}")
            print(f"\nStart the agent with: vpsmon-agent.py")
        else:
            print(f"Enrollment failed: {result.get('error', 'unknown')}")
            sys.exit(1)
        return

    # Override config from CLI
    if args.server:
        cfg["server"] = args.server
    if args.token:
        cfg["agent_token"] = args.token

    if not cfg.get("server") or not cfg.get("agent_token"):
        print("Error: not enrolled. Run with --enroll first, or provide --server and --token")
        sys.exit(1)

    main_loop(cfg)


if __name__ == "__main__":
    main()
