import asyncio
import json
import logging
import re
import time

from ..utils import RingBuffer
from .. import db

log = logging.getLogger(__name__)

_buffer = RingBuffer(500)
_process = None
_running = False
_subscribers = set()

# Regex patterns for security events
_SSH_SUCCESS = re.compile(
    r"Accepted (\w+) for (\w+) from ([\d.]+) port (\d+)"
)
_SSH_FAIL = re.compile(
    r"Failed (\w+) for (?:invalid user )?(\S+) from ([\d.]+) port (\d+)"
)
_SSH_INVALID_USER = re.compile(
    r"Invalid user (\S+) from ([\d.]+)"
)
_SUDO = re.compile(
    r"(\w+) : .* COMMAND=(.*)"
)
_SESSION_OPENED = re.compile(
    r"pam_unix\(\S+:session\): session opened for user (\S+)"
)
_SESSION_CLOSED = re.compile(
    r"pam_unix\(\S+:session\): session closed for user (\S+)"
)


def get_recent(n: int = 100) -> list:
    return _buffer.get_recent(n)


def subscribe(queue: asyncio.Queue):
    _subscribers.add(queue)


def unsubscribe(queue: asyncio.Queue):
    _subscribers.discard(queue)


async def start():
    global _process, _running
    _running = True
    try:
        _process = await asyncio.create_subprocess_exec(
            "journalctl", "--follow", "--no-pager", "-o", "json",
            "--since", "now",
            "-u", "sshd", "-u", "sudo", "-t", "sshd", "-t", "sudo",
            "--priority", "0..6",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log.info("Security event collector started")
        asyncio.create_task(_read_loop())
    except FileNotFoundError:
        log.info("journalctl not found, security monitoring disabled")
        _running = False


async def _read_loop():
    global _running
    while _running and _process and _process.stdout:
        try:
            line = await asyncio.wait_for(
                _process.stdout.readline(), timeout=5.0
            )
            if not line:
                break
            try:
                entry = json.loads(line)
                message = entry.get("MESSAGE", "")
                event = _parse_security_event(message, entry)
                if event:
                    _buffer.append(event)
                    # Persist to DB
                    try:
                        await db.execute(
                            "INSERT INTO security_events (ts, event_type, severity, source_ip, username, message, raw_log) VALUES (?,?,?,?,?,?,?)",
                            (event["ts"], event["type"], event["severity"],
                             event.get("source_ip", ""), event.get("username", ""),
                             event["message"], message)
                        )
                    except Exception:
                        pass
                    for q in list(_subscribers):
                        try:
                            q.put_nowait(event)
                        except asyncio.QueueFull:
                            pass
            except (json.JSONDecodeError, ValueError):
                pass
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            log.warning(f"Security read error: {e}")
            await asyncio.sleep(1)


def _parse_security_event(message: str, entry: dict) -> dict | None:
    ts = int(entry.get("__REALTIME_TIMESTAMP", str(int(time.time() * 1e6)))) // 1_000_000

    m = _SSH_SUCCESS.search(message)
    if m:
        return {
            "ts": ts, "type": "ssh_login", "severity": "info",
            "source_ip": m.group(3), "username": m.group(2),
            "auth_method": m.group(1),
            "message": f"SSH login: {m.group(2)} from {m.group(3)} ({m.group(1)})",
        }

    m = _SSH_FAIL.search(message)
    if m:
        return {
            "ts": ts, "type": "ssh_fail", "severity": "warning",
            "source_ip": m.group(3), "username": m.group(2),
            "auth_method": m.group(1),
            "message": f"Failed SSH: {m.group(2)} from {m.group(3)} ({m.group(1)})",
        }

    m = _SSH_INVALID_USER.search(message)
    if m:
        return {
            "ts": ts, "type": "ssh_invalid_user", "severity": "warning",
            "source_ip": m.group(2), "username": m.group(1),
            "message": f"Invalid SSH user: {m.group(1)} from {m.group(2)}",
        }

    m = _SUDO.search(message)
    if m:
        return {
            "ts": ts, "type": "sudo", "severity": "info",
            "username": m.group(1), "source_ip": "",
            "message": f"sudo: {m.group(1)} ran {m.group(2)[:100]}",
        }

    m = _SESSION_OPENED.search(message)
    if m:
        return {
            "ts": ts, "type": "session_open", "severity": "info",
            "username": m.group(1), "source_ip": "",
            "message": f"Session opened for {m.group(1)}",
        }

    return None


async def get_history(hours: int = 24, event_type: str = None) -> list:
    cutoff = int(time.time()) - (hours * 3600)
    if event_type:
        rows = await db.execute(
            "SELECT * FROM security_events WHERE ts > ? AND event_type = ? ORDER BY ts DESC LIMIT 500",
            (cutoff, event_type)
        )
    else:
        rows = await db.execute(
            "SELECT * FROM security_events WHERE ts > ? ORDER BY ts DESC LIMIT 500",
            (cutoff,)
        )
    return rows


async def stop():
    global _running, _process
    _running = False
    if _process:
        try:
            _process.terminate()
            await asyncio.wait_for(_process.wait(), timeout=5)
        except Exception:
            if _process:
                _process.kill()
        _process = None
