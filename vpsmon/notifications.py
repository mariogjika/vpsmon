"""Notification dispatcher for alerts — Slack, Discord, Telegram, Email, generic webhook."""
import asyncio
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage

import aiohttp

from . import db

log = logging.getLogger(__name__)

SEVERITY_LEVEL = {"info": 0, "warning": 1, "critical": 2}


async def list_channels() -> list[dict]:
    rows = await db.execute("SELECT * FROM notification_channels ORDER BY name")
    return rows


async def add_channel(name: str, kind: str, config_dict: dict,
                     min_severity: str = "warning") -> int:
    import time
    now = int(time.time())
    await db.execute(
        """INSERT INTO notification_channels (name, kind, config, enabled, min_severity, created_at)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (name, kind, json.dumps(config_dict), min_severity, now)
    )
    rows = await db.execute("SELECT id FROM notification_channels WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))
    return rows[0]["id"] if rows else 0


async def update_channel(channel_id: int, **fields):
    allowed = {"name", "kind", "config", "enabled", "min_severity"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "config" in updates and isinstance(updates["config"], dict):
        updates["config"] = json.dumps(updates["config"])
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [channel_id]
    await db.execute(f"UPDATE notification_channels SET {cols} WHERE id = ?", tuple(vals))


async def delete_channel(channel_id: int):
    await db.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))


async def dispatch(alert: dict):
    """Send an alert to all channels that match its severity."""
    channels = await list_channels()
    sev_level = SEVERITY_LEVEL.get(alert.get("severity", "info"), 0)

    for ch in channels:
        if not ch.get("enabled", 1):
            continue
        min_sev = SEVERITY_LEVEL.get(ch.get("min_severity", "warning"), 1)
        if sev_level < min_sev:
            continue
        try:
            cfg = json.loads(ch.get("config", "{}"))
        except json.JSONDecodeError:
            continue

        kind = ch.get("kind", "")
        try:
            if kind == "slack":
                await send_slack(cfg, alert)
            elif kind == "discord":
                await send_discord(cfg, alert)
            elif kind == "telegram":
                await send_telegram(cfg, alert)
            elif kind == "webhook":
                await send_webhook(cfg, alert)
            elif kind == "email":
                await send_email(cfg, alert)
        except Exception as e:
            log.warning(f"Notification channel {ch.get('name')} failed: {e}")


def _format_text(alert: dict) -> tuple[str, str]:
    """Return (title, body) for an alert."""
    sev = (alert.get("severity") or "info").upper()
    title = f"[{sev}] VPSMon Alert"
    lines = [
        alert.get("message", "Alert triggered"),
        f"Metric: {alert.get('metric_name', 'n/a')}",
    ]
    if alert.get("current_value") is not None:
        lines.append(f"Value: {alert['current_value']}")
    if alert.get("threshold") is not None:
        lines.append(f"Threshold: {alert['threshold']}")
    return title, "\n".join(lines)


async def send_slack(cfg: dict, alert: dict):
    url = cfg.get("webhook_url")
    if not url:
        return
    title, body = _format_text(alert)
    color = {"critical": "#ef4444", "warning": "#f59e0b", "info": "#3b82f6"}.get(alert.get("severity"), "#6366f1")
    payload = {
        "attachments": [{
            "color": color,
            "title": title,
            "text": body,
            "footer": "VPSMon",
            "ts": alert.get("ts"),
        }]
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            await r.read()


async def send_discord(cfg: dict, alert: dict):
    url = cfg.get("webhook_url")
    if not url:
        return
    title, body = _format_text(alert)
    color = {"critical": 0xef4444, "warning": 0xf59e0b, "info": 0x3b82f6}.get(alert.get("severity"), 0x6366f1)
    payload = {
        "embeds": [{
            "title": title,
            "description": body,
            "color": color,
            "footer": {"text": "VPSMon"},
        }]
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            await r.read()


async def send_telegram(cfg: dict, alert: dict):
    token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        return
    title, body = _format_text(alert)
    text = f"*{title}*\n```\n{body}\n```"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            await r.read()


async def send_webhook(cfg: dict, alert: dict):
    url = cfg.get("url")
    if not url:
        return
    headers = cfg.get("headers", {}) or {}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=alert, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=10)) as r:
            await r.read()


async def send_email(cfg: dict, alert: dict):
    """SMTP email sender. Runs blocking SMTP in executor."""
    def _send():
        msg = EmailMessage()
        title, body = _format_text(alert)
        msg["Subject"] = title
        msg["From"] = cfg.get("from_addr", "vpsmon@localhost")
        msg["To"] = cfg.get("to_addr", "")
        msg.set_content(body)

        host = cfg.get("smtp_host", "localhost")
        port = int(cfg.get("smtp_port", 587))
        user = cfg.get("smtp_user", "")
        password = cfg.get("smtp_password", "")
        use_tls = cfg.get("use_tls", True)

        if use_tls:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)

    await asyncio.get_event_loop().run_in_executor(None, _send)


async def test_channel(channel_id: int) -> dict:
    """Send a test alert to a specific channel."""
    test_alert = {
        "severity": "info",
        "message": "VPSMon test notification — this confirms the channel is working!",
        "metric_name": "test",
        "current_value": 0,
        "threshold": 0,
        "ts": __import__("time").time(),
        "alert_type": "test",
    }
    rows = await db.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,))
    if not rows:
        return {"ok": False, "error": "channel not found"}
    ch = rows[0]
    try:
        cfg = json.loads(ch.get("config", "{}"))
        kind = ch.get("kind", "")
        if kind == "slack": await send_slack(cfg, test_alert)
        elif kind == "discord": await send_discord(cfg, test_alert)
        elif kind == "telegram": await send_telegram(cfg, test_alert)
        elif kind == "webhook": await send_webhook(cfg, test_alert)
        elif kind == "email": await send_email(cfg, test_alert)
        else: return {"ok": False, "error": f"unknown kind: {kind}"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
