import asyncio
import json
import logging
import subprocess
import time

from ..utils import RingBuffer
from .. import config

log = logging.getLogger(__name__)

_buffer = RingBuffer(config.LOG_BUFFER_SIZE)
_process = None
_running = False
_subscribers = set()


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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log.info("Log collector started (journalctl)")
        asyncio.create_task(_read_loop())
    except FileNotFoundError:
        log.info("journalctl not found, log collection disabled")
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
                parsed = {
                    "ts": int(entry.get("__REALTIME_TIMESTAMP", str(int(time.time() * 1e6)))),
                    "unit": entry.get("_SYSTEMD_UNIT", entry.get("SYSLOG_IDENTIFIER", "?")),
                    "priority": int(entry.get("PRIORITY", 6)),
                    "message": entry.get("MESSAGE", ""),
                    "hostname": entry.get("_HOSTNAME", ""),
                }
                # Convert microsecond ts to seconds
                parsed["ts"] = parsed["ts"] // 1_000_000
                _buffer.append(parsed)
                for q in list(_subscribers):
                    try:
                        q.put_nowait(parsed)
                    except asyncio.QueueFull:
                        pass
            except (json.JSONDecodeError, ValueError):
                pass
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            log.warning(f"Log read error: {e}")
            await asyncio.sleep(1)


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
