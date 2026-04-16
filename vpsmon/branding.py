"""White-label branding — custom logo, name, colors, footer text.

Stored in app_settings table, served to frontend via /api/branding.
"""
import json
import logging
from typing import Optional

from . import db

log = logging.getLogger(__name__)

DEFAULT_BRANDING = {
    "product_name": "VPSMon",
    "company_name": "Solverix",
    "company_url": "https://solverix.io",
    "logo_url": "",  # Empty = use default SVG
    "accent_color": "#4f46e5",
    "login_subtitle": "Server monitoring by Solverix",
    "footer_text": "Open-source server monitoring",
    "status_page_title": "Service Status",
    "favicon_url": "/static/icon.svg",
}


async def get_branding() -> dict:
    """Return current branding config."""
    try:
        rows = await db.execute("SELECT value FROM app_settings WHERE key = 'branding'")
        if rows:
            stored = json.loads(rows[0]["value"] if isinstance(rows[0], dict) else rows[0][0])
            return {**DEFAULT_BRANDING, **stored}
    except Exception:
        pass
    return dict(DEFAULT_BRANDING)


async def update_branding(fields: dict):
    """Update branding config (partial update)."""
    current = await get_branding()
    allowed = set(DEFAULT_BRANDING.keys())
    for k, v in fields.items():
        if k in allowed:
            current[k] = v
    try:
        await db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('branding', ?)",
            (json.dumps(current),),
        )
    except Exception as e:
        log.warning(f"Branding save error: {e}")


async def reset_branding():
    """Reset to defaults."""
    try:
        await db.execute("DELETE FROM app_settings WHERE key = 'branding'")
    except Exception:
        pass
