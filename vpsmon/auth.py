import secrets
import time
import logging
from collections import defaultdict

import bcrypt
from aiohttp import web

from . import db, config

log = logging.getLogger(__name__)

_rate_limits: dict[str, list[float]] = defaultdict(list)


async def create_user(username: str, password: str):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
    await db.execute(
        "INSERT OR REPLACE INTO users (username, password, created_at) VALUES (?,?,?)",
        (username, hashed.decode(), int(time.time()))
    )
    log.info(f"User created: {username}")


async def verify_password(username: str, password: str) -> dict | None:
    rows = await db.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    )
    if not rows:
        return None
    user = rows[0]
    if bcrypt.checkpw(password.encode(), user["password"].encode()):
        return user
    return None


async def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    now = int(time.time())
    await db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, now, now + config.SESSION_MAX_AGE)
    )
    return token


async def validate_session(token: str) -> dict | None:
    if not token:
        return None
    rows = await db.execute(
        "SELECT s.*, u.username FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, int(time.time()))
    )
    return rows[0] if rows else None


async def delete_session(token: str):
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    window_start = now - config.LOGIN_RATE_WINDOW
    attempts = _rate_limits[ip]
    _rate_limits[ip] = [t for t in attempts if t > window_start]
    return len(_rate_limits[ip]) < config.LOGIN_RATE_LIMIT


def record_attempt(ip: str):
    _rate_limits[ip].append(time.time())


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # Allow login page and static assets without auth
    path = request.path
    if path in ("/api/auth/login", "/api/auth/check", "/api/health",
                "/sw.js", "/manifest.json", "/status", "/api/status",
                "/metrics") or \
       path.startswith("/static/") or path == "/" or path == "/favicon.ico" or \
       path.startswith("/ws/"):
        return await handler(request)

    # Check session cookie
    token = request.cookies.get("session")
    session = await validate_session(token)
    if session:
        request["user"] = session
        return await handler(request)

    # Check header token (for API clients) — session first, then long-lived API token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        session = await validate_session(token)
        if session:
            request["user"] = session
            return await handler(request)
        # Try API token
        try:
            from . import tokens as _tokens
            tok = await _tokens.validate(token)
            if tok:
                request["user"] = {"username": tok["username"], "api_token": True,
                                   "scopes": tok.get("scopes", "read")}
                # Read-scope tokens can only call GET
                if tok.get("scopes", "read") == "read" and request.method not in ("GET", "HEAD"):
                    return web.json_response({"error": "read-only token"}, status=403)
                return await handler(request)
        except Exception:
            pass

    # Not authenticated
    if path.startswith("/api/"):
        return web.json_response({"error": "unauthorized"}, status=401)
    # Redirect to login for page requests
    raise web.HTTPFound("/")


async def ensure_admin_exists():
    rows = await db.execute("SELECT COUNT(*) as cnt FROM users")
    if rows[0]["cnt"] == 0:
        await create_user("admin", "admin")
        log.warning("Default admin user created (username: admin, password: admin) — CHANGE THIS!")


async def get_all_users() -> list:
    """Return all users without password hashes."""
    rows = await db.execute("SELECT id, username, created_at FROM users ORDER BY id")
    return rows


async def delete_user(user_id: int) -> bool:
    """Delete a user and all their sessions."""
    # Prevent deleting the last user
    rows = await db.execute("SELECT COUNT(*) as cnt FROM users")
    if rows[0]["cnt"] <= 1:
        return False
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    log.info(f"User {user_id} deleted")
    return True
