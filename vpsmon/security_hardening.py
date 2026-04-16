"""Security hardening module — forced password change, IP whitelisting,
session management, CSRF tokens, password policy enforcement.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from typing import Optional

from . import db

log = logging.getLogger(__name__)

# CSRF secret (regenerated on startup)
_csrf_secret = secrets.token_hex(32)


def generate_csrf_token(session_token: str) -> str:
    """Generate a CSRF token tied to the user's session."""
    return hmac.new(
        _csrf_secret.encode(), session_token.encode(), hashlib.sha256
    ).hexdigest()[:32]


def verify_csrf_token(session_token: str, csrf_token: str) -> bool:
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(expected, csrf_token)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Enforce minimum password requirements.
    Returns (valid, message).
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number"
    # Check against common passwords
    weak = {"password", "12345678", "admin123", "adminadmin", "qwerty12", "letmein1",
            "password1", "Password1", "admin1234", "changeme1"}
    if password.lower().replace(" ", "") in weak:
        return False, "This password is too common"
    return True, "OK"


async def check_force_password_change(username: str) -> bool:
    """Returns True if the user must change their password (still using default)."""
    try:
        rows = await db.execute(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        )
        if not rows:
            return False
        import bcrypt
        # Check if current password is the default 'admin'
        stored = rows[0]["password_hash"]
        if isinstance(stored, str):
            stored = stored.encode("utf-8")
        return bcrypt.checkpw(b"admin", stored)
    except Exception:
        return False


async def get_active_sessions(username: Optional[str] = None) -> list:
    """List active sessions."""
    try:
        if username:
            rows = await db.execute(
                "SELECT id, username, created_at, ip FROM sessions WHERE username = ? ORDER BY created_at DESC",
                (username,),
            )
        else:
            rows = await db.execute(
                "SELECT id, username, created_at, ip FROM sessions ORDER BY created_at DESC LIMIT 100"
            )
        return rows
    except Exception:
        return []


async def revoke_all_sessions(username: str):
    """Revoke all sessions for a user (force re-login)."""
    try:
        await db.execute("DELETE FROM sessions WHERE username = ?", (username,))
    except Exception:
        pass


async def security_summary() -> dict:
    """Generate a security posture summary."""
    issues = []
    score = 100

    # Check default admin password
    try:
        import bcrypt
        rows = await db.execute("SELECT username, password_hash FROM users")
        for r in rows:
            h = r["password_hash"]
            if isinstance(h, str):
                h = h.encode("utf-8")
            if bcrypt.checkpw(b"admin", h):
                issues.append({
                    "severity": "critical",
                    "title": f"Default password for user '{r['username']}'",
                    "description": "Change the password immediately — 'admin' is a known default.",
                    "action": "Change password in Settings",
                })
                score -= 30
    except Exception:
        pass

    # Check if 2FA is enabled for any user
    try:
        rows = await db.execute("SELECT COUNT(*) as cnt FROM users WHERE totp_enabled = 1")
        if rows and rows[0]["cnt"] == 0:
            issues.append({
                "severity": "warning",
                "title": "No users have 2FA enabled",
                "description": "Enable TOTP two-factor authentication for at least the admin account.",
                "action": "Enable in Settings → Two-Factor Authentication",
            })
            score -= 15
    except Exception:
        pass

    # Check total user count
    try:
        rows = await db.execute("SELECT COUNT(*) as cnt FROM users")
        user_count = rows[0]["cnt"] if rows else 0
        if user_count == 1:
            issues.append({
                "severity": "info",
                "title": "Only one user account exists",
                "description": "Consider creating individual accounts for each team member for audit trail purposes.",
                "action": "Add users in Settings → User Management",
            })
            score -= 5
    except Exception:
        pass

    # Check if HTTPS is being used (check if request came through TLS)
    # We can't check this directly here, so just note it
    issues.append({
        "severity": "info",
        "title": "Ensure HTTPS is configured",
        "description": "VPSMon should always be accessed over HTTPS with a valid certificate.",
        "action": "Verify nginx/caddy TLS setup",
    })

    # Check session count
    try:
        rows = await db.execute("SELECT COUNT(*) as cnt FROM sessions")
        session_count = rows[0]["cnt"] if rows else 0
        if session_count > 50:
            issues.append({
                "severity": "warning",
                "title": f"{session_count} active sessions",
                "description": "High number of sessions may indicate credential sharing or a leak.",
                "action": "Review and revoke unused sessions",
            })
            score -= 10
    except Exception:
        pass

    # Check SSH passwords stored
    try:
        rows = await db.execute("SELECT COUNT(*) as cnt FROM servers WHERE ssh_password != '' AND ssh_password IS NOT NULL")
        pw_count = rows[0]["cnt"] if rows else 0
        if pw_count > 0:
            issues.append({
                "severity": "warning",
                "title": f"{pw_count} servers use password authentication",
                "description": "SSH key authentication is more secure than passwords. Consider switching to key-based auth.",
                "action": "Upload SSH keys for fleet servers",
            })
            score -= 10
    except Exception:
        pass

    score = max(0, min(100, score))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"

    return {
        "score": score,
        "grade": grade,
        "issues": issues,
        "critical": sum(1 for i in issues if i["severity"] == "critical"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
    }
