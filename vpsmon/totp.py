"""Minimal TOTP implementation (RFC 6238) — no external dependencies.

Compatible with Google Authenticator, Authy, 1Password, etc.
"""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    """Generate a new random base32 secret (20 bytes)."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    # Decode base32 secret (handle missing padding)
    pad_len = (-len(secret)) % 8
    key = base64.b32decode(secret.upper() + "=" * pad_len)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset+4])[0] & 0x7fffffff) % (10 ** digits)
    return str(code).zfill(digits)


def generate_code(secret: str, at_time: float = None) -> str:
    if at_time is None:
        at_time = time.time()
    counter = int(at_time // 30)
    return _hotp(secret, counter)


def verify(secret: str, code: str, at_time: float = None, window: int = 1) -> bool:
    """Verify a TOTP code with a window of ±window * 30s for clock drift."""
    if not secret or not code or len(code) != 6:
        return False
    if at_time is None:
        at_time = time.time()
    current = int(at_time // 30)
    code = code.strip()
    for delta in range(-window, window + 1):
        expected = _hotp(secret, current + delta)
        if hmac.compare_digest(expected, code):
            return True
    return False


def otpauth_uri(secret: str, username: str, issuer: str = "VPSMon") -> str:
    """Build otpauth:// URI for QR code generation."""
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
