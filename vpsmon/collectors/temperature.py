import asyncio
import psutil

_latest = {"sensors": [], "fans": [], "available": False}


def get_latest() -> dict:
    return dict(_latest)


async def collect_once() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _collect_sync)


def _collect_sync() -> dict:
    result = {"sensors": [], "fans": [], "available": False}
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
        for name, entries in (temps or {}).items():
            for e in entries:
                result["sensors"].append({
                    "name": f"{name}:{e.label or 'sensor'}",
                    "current": e.current,
                    "high": e.high,
                    "critical": e.critical,
                })
        if result["sensors"]:
            result["available"] = True
        fans = psutil.sensors_fans() if hasattr(psutil, 'sensors_fans') else {}
        for name, entries in (fans or {}).items():
            for e in entries:
                result["fans"].append({"name": f"{name}:{e.label}", "rpm": e.current})
    except Exception:
        pass
    _latest.clear()
    _latest.update(result)
    return result
