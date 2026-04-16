import asyncio
import psutil

_latest = {"by_cpu": [], "by_mem": [], "total": 0}


def get_latest() -> dict:
    return dict(_latest)


async def collect_once() -> dict:
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _collect_sync)
    _latest.update(data)
    return data


def _collect_sync() -> dict:
    procs = []
    attrs = ["pid", "name", "username", "cpu_percent", "memory_percent",
             "memory_info", "status", "create_time", "cmdline", "num_threads"]
    for p in psutil.process_iter(attrs=attrs, ad_value=None):
        info = p.info
        if info["pid"] == 0:
            continue
        cmd = ""
        if info.get("cmdline"):
            cmd = " ".join(info["cmdline"][:5])
        mem_rss = 0
        if info.get("memory_info"):
            mem_rss = info["memory_info"].rss
        procs.append({
            "pid": info["pid"],
            "name": info["name"] or "?",
            "user": info["username"] or "?",
            "cpu": info["cpu_percent"] or 0,
            "mem_pct": info["memory_percent"] or 0,
            "mem_rss": mem_rss,
            "status": info["status"] or "?",
            "threads": info["num_threads"] or 0,
            "cmd": cmd[:200],
        })
    by_cpu = sorted(procs, key=lambda p: p["cpu"], reverse=True)[:50]
    by_mem = sorted(procs, key=lambda p: p["mem_rss"], reverse=True)[:50]
    return {"by_cpu": by_cpu, "by_mem": by_mem, "total": len(procs)}
