import asyncio
import json
import logging
import time

import aiohttp
from aiohttp import web

from .. import alerts, auth, config
from ..collectors import system, docker_mon, processes, network, logs, security

log = logging.getLogger(__name__)

_clients: set[web.WebSocketResponse] = set()


def setup_websocket(app: web.Application):
    app.router.add_get("/ws", handle_ws)


async def handle_ws(request: web.Request):
    # Auth check for WebSocket
    token = request.query.get("token") or request.cookies.get("session")
    session = await auth.validate_session(token)
    if not session:
        return web.Response(status=401, text="Unauthorized")

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    _clients.add(ws)
    subscriptions = {"system", "docker", "processes", "network", "alerts"}
    log_queue = asyncio.Queue(maxsize=200)
    sec_queue = asyncio.Queue(maxsize=200)
    alert_queue = asyncio.Queue(maxsize=200)
    log_subscribed = False
    sec_subscribed = False

    try:
        # Send initial state
        await _send_json(ws, "system", system.get_latest())
        await _send_json(ws, "docker", {
            "available": docker_mon.is_available(),
            "containers": docker_mon.get_latest(),
        })
        await _send_json(ws, "processes", processes.get_latest())
        await _send_json(ws, "network", network.get_latest())

        # Start push tasks
        push_task = asyncio.create_task(_push_loop(ws, subscriptions))
        log_push_task = None
        sec_push_task = None

        # Subscribe to alerts by default
        alerts.subscribe(alert_queue)
        alert_push_task = asyncio.create_task(
            _push_alert_stream(ws, alert_queue)
        )

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    cmd = data.get("cmd")
                    if cmd == "subscribe":
                        channels = data.get("channels", [])
                        subscriptions.update(channels)
                        if "logs" in channels and not log_subscribed:
                            logs.subscribe(log_queue)
                            log_subscribed = True
                            log_push_task = asyncio.create_task(
                                _push_log_stream(ws, log_queue)
                            )
                            # Send recent logs
                            recent = logs.get_recent(100)
                            await _send_json(ws, "logs_history", recent)
                        if "security" in channels and not sec_subscribed:
                            security.subscribe(sec_queue)
                            sec_subscribed = True
                            sec_push_task = asyncio.create_task(
                                _push_sec_stream(ws, sec_queue)
                            )
                            recent = security.get_recent(50)
                            await _send_json(ws, "security_history", recent)
                    elif cmd == "unsubscribe":
                        channels = data.get("channels", [])
                        subscriptions.difference_update(channels)
                        if "logs" in channels and log_subscribed:
                            logs.unsubscribe(log_queue)
                            log_subscribed = False
                            if log_push_task:
                                log_push_task.cancel()
                        if "security" in channels and sec_subscribed:
                            security.unsubscribe(sec_queue)
                            sec_subscribed = False
                            if sec_push_task:
                                sec_push_task.cancel()
                except json.JSONDecodeError:
                    pass
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
    finally:
        push_task.cancel()
        alert_push_task.cancel()
        alerts.unsubscribe(alert_queue)
        if log_subscribed:
            logs.unsubscribe(log_queue)
        if sec_subscribed:
            security.unsubscribe(sec_queue)
        _clients.discard(ws)
        log.debug(f"WebSocket client disconnected, {len(_clients)} remaining")

    return ws


async def _push_loop(ws: web.WebSocketResponse, subscriptions: set):
    intervals = {
        "system": config.WS_SYSTEM_INTERVAL,
        "docker": config.WS_DOCKER_INTERVAL,
        "processes": config.WS_PROCESS_INTERVAL,
        "network": config.WS_NETWORK_INTERVAL,
    }
    last_push = {k: 0.0 for k in intervals}

    while not ws.closed:
        now = time.time()
        for channel, interval in intervals.items():
            if channel not in subscriptions:
                continue
            if now - last_push[channel] < interval:
                continue
            last_push[channel] = now
            try:
                if channel == "system":
                    await _send_json(ws, "system", system.get_latest())
                elif channel == "docker":
                    await _send_json(ws, "docker", {
                        "available": docker_mon.is_available(),
                        "containers": docker_mon.get_latest(),
                    })
                elif channel == "processes":
                    await _send_json(ws, "processes", processes.get_latest())
                elif channel == "network":
                    await _send_json(ws, "network", network.get_latest())
            except Exception:
                break
        await asyncio.sleep(1)


async def _push_log_stream(ws: web.WebSocketResponse, queue: asyncio.Queue):
    while not ws.closed:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=5.0)
            await _send_json(ws, "log", entry)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


async def _push_sec_stream(ws: web.WebSocketResponse, queue: asyncio.Queue):
    while not ws.closed:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=5.0)
            await _send_json(ws, "security_event", entry)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


async def _push_alert_stream(ws: web.WebSocketResponse, queue: asyncio.Queue):
    while not ws.closed:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=5.0)
            await _send_json(ws, "alert", entry)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


async def _send_json(ws: web.WebSocketResponse, channel: str, data):
    if ws.closed:
        return
    try:
        await ws.send_json({"channel": channel, "data": data, "ts": time.time()})
    except Exception:
        pass


def get_client_count() -> int:
    return len(_clients)
