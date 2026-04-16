"""API tokens — long-lived auth for scripts & integrations."""
import hashlib
import secrets
import time
from typing import Optional

from . import db


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS api_tokens (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT NOT NULL,
        name        TEXT NOT NULL,
        token_hash  TEXT UNIQUE NOT NULL,
        prefix      TEXT NOT NULL,
        scopes      TEXT DEFAULT 'read',
        created_at  INTEGER NOT NULL,
        last_used   INTEGER,
        expires_at  INTEGER
    )""")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create(username: str, name: str, scopes: str = "read", days: int = 365) -> dict:
    """Returns the raw token ONCE. Only hash is stored."""
    await _ensure_schema()
    raw = "vpsm_" + secrets.token_urlsafe(32)
    token_hash = _hash(raw)
    prefix = raw[:12] + "..."
    expires_at = int(time.time()) + days * 86400 if days > 0 else None
    await db.execute(
        "INSERT INTO api_tokens (username, name, token_hash, prefix, scopes, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (username, name, token_hash, prefix, scopes, int(time.time()), expires_at),
    )
    rows = await db.execute("SELECT id FROM api_tokens WHERE token_hash = ?", (token_hash,))
    return {"id": rows[0]["id"], "token": raw, "prefix": prefix, "expires_at": expires_at}


async def validate(raw: str) -> Optional[dict]:
    """Return the token record if valid (and update last_used)."""
    if not raw or not raw.startswith("vpsm_"):
        return None
    h = _hash(raw)
    rows = await db.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (h,))
    if not rows:
        return None
    tok = rows[0]
    if tok.get("expires_at") and tok["expires_at"] < int(time.time()):
        return None
    await db.execute("UPDATE api_tokens SET last_used = ? WHERE id = ?",
                     (int(time.time()), tok["id"]))
    return tok


async def list_tokens(username: Optional[str] = None) -> list:
    await _ensure_schema()
    if username:
        return await db.execute(
            "SELECT id, username, name, prefix, scopes, created_at, last_used, expires_at "
            "FROM api_tokens WHERE username = ? ORDER BY created_at DESC",
            (username,),
        )
    return await db.execute(
        "SELECT id, username, name, prefix, scopes, created_at, last_used, expires_at "
        "FROM api_tokens ORDER BY created_at DESC"
    )


async def revoke(token_id: int, username: Optional[str] = None):
    if username:
        await db.execute("DELETE FROM api_tokens WHERE id = ? AND username = ?", (token_id, username))
    else:
        await db.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
