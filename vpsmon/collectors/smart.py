import asyncio
import json
import logging
import shutil

log = logging.getLogger(__name__)

_latest = {"available": False, "disks": []}


def get_latest() -> dict:
    return dict(_latest)


async def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return -1, "", "timeout"
        return proc.returncode or 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as e:
        return -1, "", str(e)


async def _discover_disks() -> list[str]:
    """Return list of /dev/<name> for physical disks via lsblk -J."""
    if not shutil.which("lsblk"):
        return []
    rc, out, _ = await _run(["lsblk", "-J", "-d", "-o", "NAME,TYPE"])
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    disks = []
    for dev in data.get("blockdevices", []) or []:
        if dev.get("type") == "disk":
            name = dev.get("name")
            if name:
                disks.append(f"/dev/{name}")
    return disks


def _parse_smart(data: dict, device: str) -> dict:
    """Extract a minimal subset from smartctl JSON."""
    entry = {
        "device": device,
        "model": data.get("model_name") or data.get("model_family") or "",
        "serial": data.get("serial_number", ""),
        "temperature": None,
        "power_on_hours": None,
        "smart_status": "unknown",
        "reallocated_sectors": None,
    }

    temp = data.get("temperature") or {}
    if isinstance(temp, dict) and temp.get("current") is not None:
        entry["temperature"] = temp.get("current")

    poh = data.get("power_on_time") or {}
    if isinstance(poh, dict) and poh.get("hours") is not None:
        entry["power_on_hours"] = poh.get("hours")

    status = data.get("smart_status") or {}
    if isinstance(status, dict):
        entry["smart_status"] = "passed" if status.get("passed") else "failed"

    # SATA attributes
    attrs = (data.get("ata_smart_attributes") or {}).get("table") or []
    for a in attrs:
        try:
            aid = a.get("id")
            name = (a.get("name") or "").lower()
            raw = (a.get("raw") or {}).get("value")
            if aid == 5 or "reallocated_sector" in name:
                entry["reallocated_sectors"] = raw
            if entry["temperature"] is None and ("temperature" in name or aid in (194, 190)):
                entry["temperature"] = raw
            if entry["power_on_hours"] is None and "power_on_hours" in name:
                entry["power_on_hours"] = raw
        except Exception:
            continue

    # NVMe health log
    nvme = data.get("nvme_smart_health_information_log") or {}
    if isinstance(nvme, dict):
        if entry["temperature"] is None and nvme.get("temperature") is not None:
            entry["temperature"] = nvme.get("temperature")
        if entry["power_on_hours"] is None and nvme.get("power_on_hours") is not None:
            entry["power_on_hours"] = nvme.get("power_on_hours")

    return entry


async def collect_once() -> dict:
    result = {"available": False, "disks": []}

    if not shutil.which("smartctl"):
        _latest.clear()
        _latest.update(result)
        return result

    devices = await _discover_disks()
    if not devices:
        _latest.clear()
        _latest.update(result)
        return result

    result["available"] = True
    for dev in devices:
        rc, out, _ = await _run(["smartctl", "-a", "-j", dev])
        # smartctl exits with non-zero codes on warnings; still try to parse if we got JSON
        if not out:
            continue
        try:
            data = json.loads(out)
        except Exception:
            continue
        try:
            result["disks"].append(_parse_smart(data, dev))
        except Exception as e:
            log.debug("smart parse failed for %s: %s", dev, e)

    _latest.clear()
    _latest.update(result)
    return result
