import asyncio
import logging
import os
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_latest = {"certificates": []}


def get_latest() -> dict:
    return dict(_latest)


def _discover_domains() -> list[str]:
    env = os.environ.get("VPSMON_TLS_DOMAINS", "").strip()
    if env:
        return [d.strip() for d in env.split(",") if d.strip()]
    domains = []
    le_dir = Path("/etc/letsencrypt/live")
    try:
        if le_dir.is_dir():
            for child in le_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    if child.name.lower() == "readme":
                        continue
                    domains.append(child.name)
    except Exception as e:
        log.debug("letsencrypt discovery failed: %s", e)
    return domains


def _format_dn(components) -> str:
    try:
        parts = []
        for rdn in components:
            for k, v in rdn:
                parts.append(f"{k}={v}")
        return ",".join(parts)
    except Exception:
        return ""


def _check_domain_sync(domain: str, port: int = 443, timeout: float = 5.0) -> dict:
    entry = {
        "domain": domain,
        "expires_at": None,
        "days_remaining": None,
        "issuer": "",
        "subject": "",
        "valid": False,
        "error": None,
    }
    try:
        ctx = ssl.create_default_context()
        # Keep verification on; but allow to still fetch the cert if verify fails
        ctx.check_hostname = True
        with socket.create_connection((domain, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                entry["valid"] = True
    except ssl.SSLCertVerificationError as e:
        entry["error"] = f"verify_failed: {e.reason if hasattr(e, 'reason') else str(e)}"
        # Attempt a non-verifying handshake to read cert info
        try:
            ctx2 = ssl._create_unverified_context()
            with socket.create_connection((domain, port), timeout=timeout) as sock:
                with ctx2.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
        except Exception as e2:
            entry["error"] = f"{entry['error']}; {e2}"
            return entry
    except (socket.gaierror, socket.timeout, ConnectionError, OSError) as e:
        entry["error"] = str(e)
        return entry
    except Exception as e:
        entry["error"] = str(e)
        return entry

    try:
        not_after = cert.get("notAfter") if cert else None
        if not_after:
            # Example: 'Dec 31 23:59:59 2027 GMT'
            dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            entry["expires_at"] = dt.isoformat()
            entry["days_remaining"] = (dt - datetime.now(timezone.utc)).days
        entry["issuer"] = _format_dn(cert.get("issuer", [])) if cert else ""
        entry["subject"] = _format_dn(cert.get("subject", [])) if cert else ""
    except Exception as e:
        entry["error"] = (entry["error"] + "; " if entry["error"] else "") + f"parse: {e}"

    return entry


async def collect_once() -> dict:
    domains = _discover_domains()
    loop = asyncio.get_event_loop()

    results = []
    for d in domains:
        try:
            res = await loop.run_in_executor(None, _check_domain_sync, d)
        except Exception as e:
            res = {
                "domain": d, "expires_at": None, "days_remaining": None,
                "issuer": "", "subject": "", "valid": False, "error": str(e),
            }
        results.append(res)

    data = {"certificates": results}
    _latest.clear()
    _latest.update(data)
    return data
