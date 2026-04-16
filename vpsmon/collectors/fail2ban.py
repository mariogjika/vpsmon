import asyncio
import logging
import re
import shutil

log = logging.getLogger(__name__)

_latest = {"available": False, "jails": []}


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


_JAIL_LIST = re.compile(r"Jail list:\s*(.*)", re.IGNORECASE)
_BANNED_IP_LIST = re.compile(r"Banned IP list:\s*(.*)", re.IGNORECASE)


async def _list_jails() -> list[str]:
    rc, out, _ = await _run(["fail2ban-client", "status"])
    if rc != 0 or not out:
        return []
    for line in out.splitlines():
        m = _JAIL_LIST.search(line)
        if m:
            raw = m.group(1).strip()
            if not raw:
                return []
            return [j.strip() for j in raw.split(",") if j.strip()]
    return []


async def _jail_status(jail: str) -> dict:
    rc, out, _ = await _run(["fail2ban-client", "status", jail])
    banned_ips = []
    if rc == 0 and out:
        for line in out.splitlines():
            m = _BANNED_IP_LIST.search(line)
            if m:
                raw = m.group(1).strip()
                if raw:
                    for ip in raw.split():
                        banned_ips.append({"ip": ip.strip(), "since": ""})
                break
    return {"name": jail, "banned": banned_ips}


async def collect_once() -> dict:
    result = {"available": False, "jails": []}
    if not shutil.which("fail2ban-client"):
        _latest.clear()
        _latest.update(result)
        return result

    try:
        jails = await _list_jails()
    except Exception as e:
        log.debug("fail2ban list failed: %s", e)
        jails = []

    result["available"] = True
    for jail in jails:
        try:
            result["jails"].append(await _jail_status(jail))
        except Exception as e:
            log.debug("fail2ban jail %s failed: %s", jail, e)

    _latest.clear()
    _latest.update(result)
    return result
