import asyncio
import gzip as gzip_mod
import logging
import time
from pathlib import Path

from aiohttp import web

from . import (agents, alerts, audit, config, databases, db, auth, docker_updates,
                 forecast, incidents, intel, notifications, push, runbooks,
                 servers, tokens, uptime, webanalytics)
from .collectors import (
    system, docker_mon, processes, network, logs, security,
    temperature, smart, tls, services, crons, fail2ban, firewall,
)
from .storage import metrics, downsample
from .api.routes import setup_routes
from .api.websocket import setup_websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("vpsmon")

_collection_tasks = []


async def on_startup(app: web.Application):
    log.info("VPSMon starting up...")

    # Init database
    await db.init_db()
    await auth.ensure_admin_exists()

    # Init web push (generates VAPID keys on first run)
    await push.init_vapid()

    # Init Docker
    await docker_mon.init()

    # Start log and security collectors
    await logs.start()
    await security.start()

    # Start downsampling pipeline
    await downsample.start()

    # Start collection loops
    _collection_tasks.append(asyncio.create_task(_system_loop()))
    _collection_tasks.append(asyncio.create_task(_docker_loop()))
    _collection_tasks.append(asyncio.create_task(_process_loop()))
    _collection_tasks.append(asyncio.create_task(_network_loop()))

    # Initial collection to warm caches
    await system.collect_once()

    # Start alert monitoring
    await alerts.start()

    # Start multi-server monitoring
    await servers.start()

    # Start uptime monitoring
    await uptime.start()

    # Start intel layer (updates/backups/ssh-keys/anomaly/custom-metrics)
    await intel.start()

    # Start database + web analytics monitoring
    await databases.start()
    await webanalytics.start()

    # Start agent management
    await agents.start()

    # Start new collector loops
    _collection_tasks.append(asyncio.create_task(_temperature_loop()))
    _collection_tasks.append(asyncio.create_task(_services_loop()))
    _collection_tasks.append(asyncio.create_task(_firewall_loop()))
    _collection_tasks.append(asyncio.create_task(_fail2ban_loop()))
    _collection_tasks.append(asyncio.create_task(_smart_loop()))
    _collection_tasks.append(asyncio.create_task(_tls_loop()))
    _collection_tasks.append(asyncio.create_task(_crons_loop()))

    # Hook notifications + push + incidents into alerts
    async def _notify_hook():
        import asyncio as _a
        q = _a.Queue(maxsize=200)
        alerts.subscribe(q)
        while True:
            try:
                alert = await q.get()
                await notifications.dispatch(alert)
                await push.dispatch_alert(alert)
                await incidents.handle_alert(alert)
            except Exception as e:
                log.warning(f"Notification dispatch error: {e}")
    _collection_tasks.append(asyncio.create_task(_notify_hook()))

    log.info(f"VPSMon ready on http://{config.HOST}:{config.PORT}")


async def on_shutdown(app: web.Application):
    log.info("VPSMon shutting down...")
    for task in _collection_tasks:
        task.cancel()
    await alerts.stop()
    await servers.stop()
    await uptime.stop()
    await intel.stop()
    await databases.stop()
    await webanalytics.stop()
    await agents.stop()
    await logs.stop()
    await security.stop()
    await downsample.stop()


async def _temperature_loop():
    while True:
        try: await temperature.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Temp collection error: {e}")
        await asyncio.sleep(30)


async def _services_loop():
    while True:
        try: await services.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Services collection error: {e}")
        await asyncio.sleep(30)


async def _firewall_loop():
    while True:
        try: await firewall.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Firewall collection error: {e}")
        await asyncio.sleep(60)


async def _fail2ban_loop():
    while True:
        try: await fail2ban.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Fail2ban collection error: {e}")
        await asyncio.sleep(60)


async def _smart_loop():
    while True:
        try: await smart.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"SMART collection error: {e}")
        await asyncio.sleep(600)  # 10 min — SMART is slow


async def _tls_loop():
    while True:
        try: await tls.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"TLS collection error: {e}")
        await asyncio.sleep(3600)  # 1 hour — certs rarely change


async def _crons_loop():
    while True:
        try: await crons.collect_once()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Cron collection error: {e}")
        await asyncio.sleep(300)  # 5 min


async def _system_loop():
    while True:
        try:
            data = await system.collect_once()
            rows = system.get_persist_rows(data)
            await metrics.insert_raw(rows)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"System collection error: {e}")
        await asyncio.sleep(config.SYSTEM_INTERVAL)


async def _docker_loop():
    while True:
        try:
            await docker_mon.collect_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Docker collection error: {e}")
        await asyncio.sleep(config.DOCKER_INTERVAL)


async def _process_loop():
    while True:
        try:
            await processes.collect_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Process collection error: {e}")
        await asyncio.sleep(config.PROCESS_INTERVAL)


async def _network_loop():
    while True:
        try:
            await network.collect_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Network collection error: {e}")
        await asyncio.sleep(config.NETWORK_INTERVAL)


@web.middleware
async def security_headers(request: web.Request, handler):
    resp = await handler(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


@web.middleware
async def gzip_middleware(request: web.Request, handler):
    resp = await handler(request)
    # Only compress JSON/text responses with a body attribute
    if not hasattr(resp, 'body') or resp.body is None:
        return resp
    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" not in accept_encoding:
        return resp
    try:
        if len(resp.body) < 256:
            return resp
    except TypeError:
        return resp
    if "Content-Encoding" in resp.headers:
        return resp
    compressed = gzip_mod.compress(resp.body, compresslevel=6)
    resp.body = compressed
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


async def index_handler(request: web.Request):
    # Check auth - if not logged in, serve login page
    token = request.cookies.get("session")
    session = await auth.validate_session(token)
    return web.FileResponse(config.STATIC_DIR / "index.html")


def create_app() -> web.Application:
    app = web.Application(middlewares=[gzip_middleware, security_headers, auth.auth_middleware])

    # Routes
    setup_routes(app)
    setup_websocket(app)

    # Static files
    app.router.add_static("/static/", config.STATIC_DIR, name="static")
    app.router.add_get("/", index_handler)

    # PWA root-scope files (service worker needs to be served from /)
    async def _sw(request):
        resp = web.FileResponse(config.STATIC_DIR / "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    async def _manifest(request):
        return web.FileResponse(config.STATIC_DIR / "manifest.json")
    app.router.add_get("/sw.js", _sw)
    app.router.add_get("/manifest.json", _manifest)

    # Lifecycle
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


def main():
    app = create_app()
    web.run_app(app, host=config.HOST, port=config.PORT, print=None)
    log.info("VPSMon stopped")


if __name__ == "__main__":
    main()
