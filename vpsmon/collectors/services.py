import asyncio
import json
import logging
import shutil

log = logging.getLogger(__name__)

_latest = {"available": False, "services": []}


def get_latest() -> dict:
    return dict(_latest)


async def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
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


async def collect_once() -> dict:
    result = {"available": False, "services": []}
    if not shutil.which("systemctl"):
        _latest.clear()
        _latest.update(result)
        return result

    rc, out, err = await _run([
        "systemctl", "list-units", "--type=service", "--all",
        "--no-pager", "--output=json",
    ])
    if rc != 0 or not out:
        _latest.clear()
        _latest.update(result)
        return result

    try:
        units = json.loads(out)
    except Exception as e:
        log.debug("failed to parse systemctl json: %s", e)
        _latest.clear()
        _latest.update(result)
        return result

    services = []
    for u in units or []:
        try:
            services.append({
                "name": u.get("unit", ""),
                "load": u.get("load", ""),
                "active": u.get("active", ""),
                "sub": u.get("sub", ""),
                "description": u.get("description", ""),
            })
        except Exception:
            continue

    result["available"] = True
    result["services"] = services
    _latest.clear()
    _latest.update(result)
    return result


async def restart_service(name: str) -> dict:
    """Restart a systemd service. Returns {ok, output, error}."""
    if not shutil.which("systemctl"):
        return {"ok": False, "output": "", "error": "systemctl not found"}
    # Safety: only allow .service units and reject suspicious chars
    if not name or any(c in name for c in (" ", ";", "&", "|", "$", "`", "\n")):
        return {"ok": False, "output": "", "error": "invalid service name"}
    rc, out, err = await _run(["systemctl", "restart", name], timeout=30.0)
    return {"ok": rc == 0, "output": out, "error": err if rc != 0 else ""}
