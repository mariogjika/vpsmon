"""Web Push (VAPID) — subscription storage and push delivery.

On startup, generates a VAPID keypair once and stores in settings table.
Subscriptions live in their own table.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Optional

from . import db

log = logging.getLogger(__name__)

_vapid = {"public": None, "private": None, "subject": "mailto:admin@solverix.io"}


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT,
        endpoint    TEXT UNIQUE NOT NULL,
        p256dh      TEXT NOT NULL,
        auth        TEXT NOT NULL,
        ua          TEXT,
        created_at  INTEGER NOT NULL,
        last_push   INTEGER
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """)


async def _get_setting(key: str) -> Optional[str]:
    rows = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    if not rows:
        return None
    row = rows[0]
    # db wrapper returns either dicts (Row factory) or tuples depending on version
    if isinstance(row, dict):
        return row.get("value")
    try:
        return row[0]
    except Exception:
        return None


async def _set_setting(key: str, value: str):
    await db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def init_vapid():
    """Generate VAPID keypair on first launch, reuse after."""
    await _ensure_schema()
    pub = await _get_setting("vapid_public")
    priv = await _get_setting("vapid_private")
    subject = await _get_setting("vapid_subject")
    if not pub or not priv:
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import serialization
            key = ec.generate_private_key(ec.SECP256R1())
            priv_raw = key.private_numbers().private_value.to_bytes(32, "big")
            pub_pt = key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            pub = _urlsafe_b64encode(pub_pt)
            priv = _urlsafe_b64encode(priv_raw)
            await _set_setting("vapid_public", pub)
            await _set_setting("vapid_private", priv)
            log.info("Generated new VAPID keypair")
        except Exception as e:
            log.error(f"VAPID keygen failed: {e}")
            return
    _vapid["public"] = pub
    _vapid["private"] = priv
    _vapid["subject"] = subject or "mailto:admin@solverix.io"


def get_public_key() -> Optional[str]:
    return _vapid["public"]


def set_subject(subject: str):
    _vapid["subject"] = subject


async def save_subject(subject: str):
    await _set_setting("vapid_subject", subject)
    set_subject(subject)


async def subscribe(username: str, endpoint: str, p256dh: str, auth_token: str, ua: str = ""):
    """Store a push subscription."""
    await db.execute(
        """INSERT OR REPLACE INTO push_subscriptions
           (username, endpoint, p256dh, auth, ua, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, endpoint, p256dh, auth_token, ua[:255], int(time.time())),
    )


async def unsubscribe(endpoint: str):
    await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


async def list_subscriptions(username: Optional[str] = None):
    if username:
        rows = await db.execute(
            "SELECT id, username, endpoint, p256dh, auth, ua, created_at, last_push FROM push_subscriptions WHERE username = ?",
            (username,),
        )
    else:
        rows = await db.execute(
            "SELECT id, username, endpoint, p256dh, auth, ua, created_at, last_push FROM push_subscriptions"
        )
    cols = ["id", "username", "endpoint", "p256dh", "auth", "ua", "created_at", "last_push"]
    return [dict(zip(cols, r)) for r in rows]


def _private_pem() -> Optional[bytes]:
    """Convert raw private key to PEM for pywebpush."""
    if not _vapid["private"]:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        pad = "=" * ((-len(_vapid["private"])) % 4)
        raw = base64.urlsafe_b64decode(_vapid["private"] + pad)
        priv_int = int.from_bytes(raw, "big")
        key = ec.derive_private_key(priv_int, ec.SECP256R1())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pem
    except Exception as e:
        log.error(f"Private key PEM conversion failed: {e}")
        return None


async def send_to_subscription(sub: dict, payload: dict) -> bool:
    """Send a push to a single subscription. Returns True on success."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("pywebpush not installed")
        return False
    pem = _private_pem()
    if not pem:
        return False
    loop = asyncio.get_event_loop()
    def _blocking():
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(payload),
                vapid_private_key=pem.decode(),
                vapid_claims={"sub": _vapid["subject"]},
                ttl=3600,
            )
            return True
        except WebPushException as e:
            # 410 Gone or 404 — stale subscription
            if e.response is not None and e.response.status_code in (404, 410):
                return "stale"
            log.warning(f"Push send error: {e}")
            return False
        except Exception as e:
            log.warning(f"Push send error: {e}")
            return False

    result = await loop.run_in_executor(None, _blocking)
    if result == "stale":
        await unsubscribe(sub["endpoint"])
        return False
    if result:
        await db.execute(
            "UPDATE push_subscriptions SET last_push = ? WHERE endpoint = ?",
            (int(time.time()), sub["endpoint"]),
        )
    return bool(result)


async def broadcast(payload: dict, username: Optional[str] = None):
    """Send a push payload to all subscriptions (optionally filter by user)."""
    subs = await list_subscriptions(username)
    if not subs:
        return 0
    results = await asyncio.gather(
        *[send_to_subscription(s, payload) for s in subs],
        return_exceptions=True,
    )
    return sum(1 for r in results if r is True)


async def dispatch_alert(alert: dict):
    """Bridge from alerts → web push broadcast."""
    try:
        sev = alert.get("severity", "info")
        payload = {
            "title": f"[{sev.upper()}] {alert.get('metric', 'Alert')}",
            "body": alert.get("message") or f"{alert.get('metric')} = {alert.get('value')}",
            "severity": sev,
            "tag": f"alert-{alert.get('metric','x')}",
            "renotify": sev == "critical",
            "url": "/#alerts",
            "data": alert,
        }
        await broadcast(payload)
    except Exception as e:
        log.warning(f"Push dispatch error: {e}")


async def test_push(username: Optional[str] = None) -> int:
    return await broadcast({
        "title": "VPSMon test",
        "body": "Push notifications are working.",
        "severity": "info",
        "url": "/",
    }, username=username)
