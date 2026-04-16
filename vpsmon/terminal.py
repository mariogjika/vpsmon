"""Web Terminal — WebSocket ↔ SSH bridge using asyncssh.

Provides a full interactive shell in the browser via xterm.js.
Each WebSocket connection spawns an SSH session to the target server.
"""
import asyncio
import json
import logging
from typing import Optional

import asyncssh
from aiohttp import web

from . import auth, servers, audit

log = logging.getLogger(__name__)


async def ws_terminal(request: web.Request):
    """WebSocket handler: /ws/terminal/{server_id}

    Query params:
      - cols, rows: initial terminal size (default 120x30)
    Server 0 or 'local' connects to localhost.
    """
    # Auth check
    token = request.cookies.get("session")
    session = await auth.validate_session(token)
    if not session:
        return web.Response(status=401, text="Unauthorized")

    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=64 * 1024)
    await ws.prepare(request)

    server_id = request.match_info.get("server_id", "local")
    cols = int(request.query.get("cols", 120))
    rows = int(request.query.get("rows", 30))

    username = session.get("username", "?")

    # Resolve connection details
    if server_id == "local" or server_id == "0":
        host = "127.0.0.1"
        port = 22
        user = "root"
        password = None
        key_path = None
        display_name = "localhost"
    else:
        srv = await servers.get_server(int(server_id))
        if not srv:
            await ws.send_str(json.dumps({"type": "error", "message": "Server not found"}))
            await ws.close()
            return ws
        host = srv["hostname"]
        port = int(srv.get("ssh_port") or 22)
        user = srv.get("ssh_user") or "root"
        password = srv.get("ssh_password") or None
        key_path = srv.get("ssh_key_path") or None
        display_name = srv["name"]

    await audit.log_event(username, "terminal.open", display_name, request.remote or "")

    try:
        # Build connection kwargs
        conn_kwargs = {
            "host": host,
            "port": port,
            "username": user,
            "known_hosts": None,  # Accept all (like StrictHostKeyChecking=no)
            "login_timeout": 15,
        }
        if password:
            conn_kwargs["password"] = password
        if key_path:
            conn_kwargs["client_keys"] = [key_path]

        async with asyncssh.connect(**conn_kwargs) as conn:
            process = await conn.create_process(
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding=None,  # binary mode
            )

            # Banner
            await ws.send_str(json.dumps({
                "type": "connected",
                "server": display_name,
                "host": host,
            }))

            # Bi-directional bridge
            async def ssh_to_ws():
                """Read from SSH stdout → send to WebSocket."""
                try:
                    while not process.stdout.at_eof():
                        data = await process.stdout.read(8192)
                        if data and not ws.closed:
                            await ws.send_bytes(data)
                except (asyncssh.BreakReceived, asyncssh.TerminalSizeChanged):
                    pass
                except Exception as e:
                    log.debug(f"ssh→ws pipe error: {e}")

            async def ws_to_ssh():
                """Read from WebSocket → write to SSH stdin."""
                try:
                    async for msg in ws:
                        if msg.type == web.WSMsgType.TEXT:
                            # JSON control messages
                            try:
                                ctrl = json.loads(msg.data)
                                if ctrl.get("type") == "resize":
                                    process.change_terminal_size(
                                        int(ctrl.get("cols", cols)),
                                        int(ctrl.get("rows", rows)),
                                    )
                                    continue
                                if ctrl.get("type") == "input":
                                    process.stdin.write(ctrl["data"].encode("utf-8"))
                                    continue
                            except (json.JSONDecodeError, KeyError):
                                pass
                            # Plain text input
                            process.stdin.write(msg.data.encode("utf-8"))
                        elif msg.type == web.WSMsgType.BINARY:
                            process.stdin.write(msg.data)
                        elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                            break
                except Exception as e:
                    log.debug(f"ws→ssh pipe error: {e}")

            # Run both pipes concurrently, stop when either ends
            done, pending = await asyncio.wait(
                [asyncio.create_task(ssh_to_ws()), asyncio.create_task(ws_to_ssh())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

    except asyncssh.DisconnectError as e:
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": f"SSH disconnected: {e}"}))
    except asyncssh.PermissionDenied:
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": "SSH permission denied"}))
    except asyncssh.ConnectionLost:
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": "SSH connection lost"}))
    except OSError as e:
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": f"Connection error: {e}"}))
    except Exception as e:
        log.warning(f"Terminal error: {e}")
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": str(e)[:200]}))
    finally:
        await audit.log_event(username, "terminal.close", display_name, request.remote or "")
        if not ws.closed:
            await ws.close()

    return ws


async def ws_file_browse(request: web.Request):
    """REST-like WebSocket for file browsing — sends commands, gets directory listings."""
    token = request.cookies.get("session")
    session = await auth.validate_session(token)
    if not session:
        return web.Response(status=401, text="Unauthorized")

    server_id = request.match_info.get("server_id", "local")
    path = request.query.get("path", "/")

    if server_id == "local" or server_id == "0":
        host = "127.0.0.1"
        port = 22
        user = "root"
        password = None
        key_path = None
    else:
        srv = await servers.get_server(int(server_id))
        if not srv:
            return web.json_response({"error": "not found"}, status=404)
        host = srv["hostname"]
        port = int(srv.get("ssh_port") or 22)
        user = srv.get("ssh_user") or "root"
        password = srv.get("ssh_password") or None
        key_path = srv.get("ssh_key_path") or None

    conn_kwargs = {
        "host": host, "port": port, "username": user,
        "known_hosts": None, "login_timeout": 10,
    }
    if password:
        conn_kwargs["password"] = password
    if key_path:
        conn_kwargs["client_keys"] = [key_path]

    try:
        async with asyncssh.connect(**conn_kwargs) as conn:
            async with conn.start_sftp_client() as sftp:
                entries = []
                for item in await sftp.readdir(path):
                    attrs = item.attrs
                    entries.append({
                        "name": item.filename,
                        "type": "dir" if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY else "file",
                        "size": attrs.size or 0,
                        "modified": attrs.mtime or 0,
                        "permissions": oct(attrs.permissions & 0o7777) if attrs.permissions else "",
                        "owner": str(attrs.uid or ""),
                        "group": str(attrs.gid or ""),
                    })
                # Sort: dirs first, then by name
                entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
                return web.json_response({"path": path, "entries": entries})
    except Exception as e:
        return web.json_response({"error": str(e)[:200]}, status=500)


async def file_read(request: web.Request):
    """Read a file from a remote server (max 1MB, text files only)."""
    token = request.cookies.get("session")
    session = await auth.validate_session(token)
    if not session:
        return web.Response(status=401, text="Unauthorized")

    server_id = request.match_info.get("server_id", "local")
    path = request.query.get("path", "")
    if not path:
        return web.json_response({"error": "path required"}, status=400)

    if server_id == "local" or server_id == "0":
        host, port, user, password, key_path = "127.0.0.1", 22, "root", None, None
    else:
        srv = await servers.get_server(int(server_id))
        if not srv:
            return web.json_response({"error": "not found"}, status=404)
        host = srv["hostname"]
        port = int(srv.get("ssh_port") or 22)
        user = srv.get("ssh_user") or "root"
        password = srv.get("ssh_password") or None
        key_path = srv.get("ssh_key_path") or None

    conn_kwargs = {"host": host, "port": port, "username": user,
                   "known_hosts": None, "login_timeout": 10}
    if password:
        conn_kwargs["password"] = password
    if key_path:
        conn_kwargs["client_keys"] = [key_path]

    try:
        async with asyncssh.connect(**conn_kwargs) as conn:
            async with conn.start_sftp_client() as sftp:
                stat = await sftp.stat(path)
                if stat.size > 1024 * 1024:
                    return web.json_response({"error": "file too large (>1MB)"}, status=400)
                data = await sftp.open(path, "r")
                content = await data.read()
                await data.close()
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    return web.json_response({"error": "binary file", "size": stat.size})
                return web.json_response({
                    "path": path, "content": text,
                    "size": stat.size, "modified": stat.mtime,
                })
    except Exception as e:
        return web.json_response({"error": str(e)[:200]}, status=500)
