import asyncio
import psutil

from ..utils import DeltaCalculator

_latest = {"connections": [], "listening": [], "per_interface": []}
_delta = DeltaCalculator()


def get_latest() -> dict:
    return dict(_latest)


async def collect_once() -> dict:
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _collect_sync)
    _latest.update(data)
    return data


def _collect_per_interface() -> list:
    """Collect per-interface network counters, addresses, and link state."""
    per_interface = []
    try:
        counters = psutil.net_io_counters(pernic=True) or {}
    except Exception:
        counters = {}
    try:
        addrs = psutil.net_if_addrs() or {}
    except Exception:
        addrs = {}
    try:
        stats = psutil.net_if_stats() or {}
    except Exception:
        stats = {}

    names = set(counters.keys()) | set(addrs.keys()) | set(stats.keys())
    for name in sorted(names):
        c = counters.get(name)
        rx_bytes = c.bytes_recv if c else 0
        tx_bytes = c.bytes_sent if c else 0
        rx_packets = c.packets_recv if c else 0
        tx_packets = c.packets_sent if c else 0

        rx_rate = _delta.rate(f"{name}:rx", rx_bytes) or 0.0
        tx_rate = _delta.rate(f"{name}:tx", tx_bytes) or 0.0

        addr_list = []
        for a in addrs.get(name, []) or []:
            try:
                fam = getattr(a.family, "name", str(a.family))
                if fam in ("AF_INET", "AF_INET6") or "INET" in str(fam):
                    addr_list.append(a.address)
            except Exception:
                continue

        is_up = False
        s = stats.get(name)
        if s is not None:
            is_up = bool(getattr(s, "isup", False))

        per_interface.append({
            "name": name,
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "rx_packets": rx_packets,
            "tx_packets": tx_packets,
            "addresses": addr_list,
            "is_up": is_up,
        })
    return per_interface


def _collect_sync() -> dict:
    connections = []
    listening = []
    try:
        conns = psutil.net_connections(kind="inet")
        for c in conns:
            entry = {
                "fd": c.fd,
                "family": "IPv4" if c.family.value == 2 else "IPv6",
                "type": "TCP" if c.type.value == 1 else "UDP",
                "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                "status": c.status if hasattr(c, "status") else "",
                "pid": c.pid,
            }
            # Resolve process name
            if c.pid:
                try:
                    entry["process"] = psutil.Process(c.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    entry["process"] = "?"
            else:
                entry["process"] = ""

            if c.status == "LISTEN":
                listening.append(entry)
            elif c.status in ("ESTABLISHED", "SYN_SENT", "SYN_RECV"):
                connections.append(entry)
    except (psutil.AccessDenied, OSError):
        pass

    per_interface = _collect_per_interface()

    return {
        "connections": connections[:200],
        "listening": listening,
        "total_established": len(connections),
        "total_listening": len(listening),
        "per_interface": per_interface,
    }
