import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_latest = {"crons": [], "timers": []}


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


_CRON_LINE = re.compile(
    r"^\s*(@\S+|(?:\S+\s+){4}\S+)\s+(.+)$"
)
_CRON_LINE_WITH_USER = re.compile(
    r"^\s*(@\S+|(?:\S+\s+){4}\S+)\s+(\S+)\s+(.+)$"
)


def _strip_comment(line: str) -> str:
    line = line.rstrip("\n")
    if not line or line.lstrip().startswith("#"):
        return ""
    return line


def _parse_crontab_text(text: str, source: str, user: str, has_user_field: bool) -> list[dict]:
    entries = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped:
            continue
        # Skip env var assignments like FOO=bar
        if "=" in stripped and not stripped.lstrip().startswith(("*", "@", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            # crude: if it looks like KEY=VALUE at the start
            first = stripped.split()[0]
            if "=" in first and not first.startswith(("*", "@")):
                continue

        if has_user_field:
            m = _CRON_LINE_WITH_USER.match(stripped)
            if m:
                schedule, u, cmd = m.group(1), m.group(2), m.group(3)
                entries.append({
                    "user": u,
                    "schedule": schedule,
                    "command": cmd.strip(),
                    "source": source,
                })
                continue
        m = _CRON_LINE.match(stripped)
        if m:
            schedule, cmd = m.group(1), m.group(2)
            entries.append({
                "user": user,
                "schedule": schedule,
                "command": cmd.strip(),
                "source": source,
            })
    return entries


async def _collect_user_crontab(user: str) -> list[dict]:
    if not shutil.which("crontab"):
        return []
    rc, out, _ = await _run(["crontab", "-l", "-u", user])
    if rc != 0 or not out:
        return []
    return _parse_crontab_text(out, f"crontab:{user}", user, has_user_field=False)


def _read_file_safely(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except Exception:
        return ""


async def _collect_system_crons() -> list[dict]:
    entries = []
    # /etc/crontab has user field
    etc_crontab = Path("/etc/crontab")
    if etc_crontab.is_file():
        entries.extend(_parse_crontab_text(
            _read_file_safely(etc_crontab), "/etc/crontab", "root", has_user_field=True
        ))

    # /etc/cron.d/* has user field
    cron_d = Path("/etc/cron.d")
    if cron_d.is_dir():
        try:
            for f in sorted(cron_d.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    entries.extend(_parse_crontab_text(
                        _read_file_safely(f), f"/etc/cron.d/{f.name}", "root",
                        has_user_field=True,
                    ))
        except Exception as e:
            log.debug("cron.d read failed: %s", e)

    # cron.{hourly,daily,weekly,monthly} are scripts, run as root on schedule
    for period in ("hourly", "daily", "weekly", "monthly"):
        d = Path(f"/etc/cron.{period}")
        if d.is_dir():
            try:
                for f in sorted(d.iterdir()):
                    if f.is_file() and os.access(f, os.X_OK):
                        entries.append({
                            "user": "root",
                            "schedule": f"@{period}",
                            "command": str(f),
                            "source": f"/etc/cron.{period}",
                        })
            except Exception as e:
                log.debug("cron.%s read failed: %s", period, e)

    return entries


async def _collect_timers() -> list[dict]:
    if not shutil.which("systemctl"):
        return []
    rc, out, _ = await _run([
        "systemctl", "list-timers", "--all", "--no-pager", "--output=json",
    ])
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    timers = []
    for t in data or []:
        try:
            timers.append({
                "unit": t.get("unit", ""),
                "activates": t.get("activates", ""),
                "next": t.get("next", ""),
                "left": t.get("left", ""),
                "last": t.get("last", ""),
                "passed": t.get("passed", ""),
            })
        except Exception:
            continue
    return timers


async def collect_once() -> dict:
    crons = []
    try:
        crons.extend(await _collect_user_crontab("root"))
    except Exception as e:
        log.debug("root crontab failed: %s", e)
    try:
        crons.extend(await _collect_system_crons())
    except Exception as e:
        log.debug("system crons failed: %s", e)

    timers = []
    try:
        timers = await _collect_timers()
    except Exception as e:
        log.debug("timers failed: %s", e)

    data = {"crons": crons, "timers": timers}
    _latest.clear()
    _latest.update(data)
    return data
