"""Scheduled email reports — weekly/monthly digest.

Generates HTML summary of uptime, alerts, resource trends, incidents.
Sends via SMTP (reuses notification email config).
"""
import asyncio
import json
import logging
import time
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

from . import db, alerts, uptime, incidents, servers, forecast, intel

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None


async def _get_smtp_config() -> Optional[dict]:
    """Pull SMTP config from notification channels (find first email channel)."""
    try:
        rows = await db.execute(
            "SELECT config FROM notification_channels WHERE kind = 'email' AND enabled = 1 LIMIT 1"
        )
        if rows:
            return json.loads(rows[0]["config"])
    except Exception:
        pass
    return None


async def generate_report(period_hours: int = 168) -> dict:
    """Generate a report covering the last `period_hours`."""
    period_label = f"{period_hours // 24}d" if period_hours >= 24 else f"{period_hours}h"

    # Uptime
    up_summary = {}
    try:
        up_summary = await uptime.summary()
    except Exception:
        pass

    # Incidents
    inc_summary = {}
    try:
        inc_summary = await incidents.summary(period_hours // 24 or 1)
    except Exception:
        pass

    # Servers
    srv_overview = {}
    try:
        srv_overview = await servers.get_overview()
    except Exception:
        pass

    # Forecasts
    forecasts = {}
    try:
        forecasts = await forecast.forecast_all()
    except Exception:
        pass

    # Updates
    updates = []
    try:
        updates = await intel.list_updates()
    except Exception:
        pass
    total_security = sum(u.get("security_updates", 0) for u in updates)

    # Alert count
    alert_count = 0
    try:
        history = await alerts.get_alert_history(period_hours)
        alert_count = len(history)
    except Exception:
        pass

    return {
        "period": period_label,
        "period_hours": period_hours,
        "generated_at": int(time.time()),
        "uptime": up_summary,
        "incidents": inc_summary,
        "servers": srv_overview,
        "forecasts": forecasts,
        "security_updates": total_security,
        "alert_count": alert_count,
    }


def _render_html(report: dict) -> str:
    """Render report to a clean HTML email."""
    up = report.get("uptime", {})
    inc = report.get("incidents", {})
    srv = report.get("servers", {})
    fc = report.get("forecasts", {})

    def _fc_row(key, label):
        f = fc.get(key, {})
        if f.get("days_until"):
            color = "#dc2626" if f["days_until"] < 7 else "#d97706" if f["days_until"] < 30 else "#059669"
            return f'<tr><td>{label}</td><td style="color:{color};font-weight:700">{f["days_until"]} days</td><td>{f.get("confidence","")}</td></tr>'
        return f'<tr><td>{label}</td><td style="color:#9ca3af">No trend</td><td></td></tr>'

    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;color:#1a1d26">
        <div style="background:#4f46e5;color:#fff;padding:24px 28px;border-radius:12px 12px 0 0">
            <h1 style="margin:0;font-size:22px;font-weight:800">VPSMon Report</h1>
            <p style="margin:6px 0 0;font-size:13px;opacity:0.85">Last {report['period']} — by Solverix</p>
        </div>
        <div style="background:#fff;padding:28px;border:1px solid #e2e4e9;border-top:none;border-radius:0 0 12px 12px">
            <h2 style="font-size:15px;color:#1a1d26;border-bottom:2px solid #e2e4e9;padding-bottom:8px">Fleet Overview</h2>
            <table style="width:100%;font-size:14px;margin-bottom:20px">
                <tr><td style="padding:6px 0;color:#555a68">Servers online</td><td style="font-weight:700">{srv.get('online',0)} / {srv.get('total',0)}</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">Uptime SLA (24h)</td><td style="font-weight:700">{up.get('sla_24h','—')}%</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">Checks up / down</td><td style="font-weight:700;color:#059669">{up.get('up',0)}</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">Alerts fired</td><td style="font-weight:700;color:{'#dc2626' if report['alert_count']>10 else '#1a1d26'}">{report['alert_count']}</td></tr>
            </table>

            <h2 style="font-size:15px;color:#1a1d26;border-bottom:2px solid #e2e4e9;padding-bottom:8px">Incidents</h2>
            <table style="width:100%;font-size:14px;margin-bottom:20px">
                <tr><td style="padding:6px 0;color:#555a68">Total incidents</td><td style="font-weight:700">{inc.get('total',0)}</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">Critical</td><td style="font-weight:700;color:#dc2626">{inc.get('critical',0)}</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">Still open</td><td style="font-weight:700">{inc.get('open',0)}</td></tr>
                <tr><td style="padding:6px 0;color:#555a68">MTTR</td><td style="font-weight:700">{inc.get('mttr_minutes','—')} min</td></tr>
            </table>

            <h2 style="font-size:15px;color:#1a1d26;border-bottom:2px solid #e2e4e9;padding-bottom:8px">Resource Forecasts</h2>
            <table style="width:100%;font-size:14px;margin-bottom:20px">
                <tr><th style="text-align:left;color:#8f95a3;font-size:11px;padding-bottom:4px">METRIC</th><th style="text-align:left;color:#8f95a3;font-size:11px">FILLS IN</th><th style="text-align:left;color:#8f95a3;font-size:11px">CONFIDENCE</th></tr>
                {_fc_row('disk_full', 'Disk')}
                {_fc_row('memory_saturation', 'Memory')}
                {_fc_row('swap_saturation', 'Swap')}
            </table>

            {'<h2 style="font-size:15px;color:#1a1d26;border-bottom:2px solid #e2e4e9;padding-bottom:8px">Security</h2><p style="font-size:14px;color:#dc2626;font-weight:700">⚠ ' + str(report["security_updates"]) + ' security updates pending across fleet</p>' if report.get('security_updates',0) > 0 else ''}

            <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e4e9;font-size:11px;color:#8f95a3;text-align:center">
                <a href="https://monitoring.solverix.io" style="color:#4f46e5;text-decoration:none;font-weight:600">Open Dashboard</a> · VPSMon by Solverix
            </div>
        </div>
    </div>
    """
    return html


async def send_report(to_email: str, period_hours: int = 168) -> bool:
    """Generate and send a report email."""
    smtp = await _get_smtp_config()
    if not smtp:
        log.warning("No SMTP config — configure an email notification channel first")
        return False

    report = await generate_report(period_hours)
    html = _render_html(report)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"VPSMon Report — {report['period']}"
    msg["From"] = smtp.get("from_email", smtp.get("username", "vpsmon@localhost"))
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    loop = asyncio.get_event_loop()
    def _send():
        try:
            s = smtplib.SMTP(smtp["host"], int(smtp.get("port", 587)), timeout=15)
            s.starttls()
            s.login(smtp.get("username", ""), smtp.get("password", ""))
            s.sendmail(msg["From"], [to_email], msg.as_string())
            s.quit()
            return True
        except Exception as e:
            log.warning(f"Report email failed: {e}")
            return False
    return await loop.run_in_executor(None, _send)


async def get_report_preview(period_hours: int = 168) -> dict:
    """Generate report data + HTML for preview (no send)."""
    report = await generate_report(period_hours)
    report["html"] = _render_html(report)
    return report
