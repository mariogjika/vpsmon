import csv
import io
import json
import time

from aiohttp import web

from .. import (agents, alerts, annotations, audit, auth, config, databases,
                 docker_updates, forecast, incidents, intel, notifications,
                 prometheus, push, reports, runbooks, security_hardening,
                 servers, terminal, tokens, totp, uptime, webanalytics)
from ..collectors import (
    system, docker_mon, processes, network, security, logs,
    temperature, smart, tls, services, crons, fail2ban, firewall,
)
from ..storage import metrics


def setup_routes(app: web.Application):
    app.router.add_post("/api/auth/login", handle_login)
    app.router.add_post("/api/auth/logout", handle_logout)
    app.router.add_get("/api/auth/check", handle_auth_check)
    app.router.add_post("/api/auth/change-password", handle_change_password)

    app.router.add_get("/api/system", handle_system)
    app.router.add_get("/api/system/history", handle_system_history)

    app.router.add_get("/api/docker", handle_docker)
    app.router.add_get("/api/docker/{container_id}/logs", handle_docker_logs)
    app.router.add_post("/api/docker/{container_id}/{action}", handle_docker_action)

    app.router.add_get("/api/processes", handle_processes)
    app.router.add_get("/api/network", handle_network)

    app.router.add_get("/api/logs", handle_logs)
    app.router.add_get("/api/security", handle_security)
    app.router.add_get("/api/security/history", handle_security_history)

    app.router.add_get("/api/overview", handle_overview)

    # Alerts
    app.router.add_get("/api/alerts", handle_alerts)
    app.router.add_post("/api/alerts/{id}/ack", handle_alert_ack)
    app.router.add_get("/api/alerts/history", handle_alerts_history)

    # System info
    app.router.add_get("/api/system/info", handle_system_info)

    # Metrics export
    app.router.add_get("/api/metrics/export", handle_metrics_export)

    # Health check (unauthenticated)
    app.router.add_get("/api/health", handle_health)

    # Docker compose projects
    app.router.add_get("/api/docker/compose", handle_docker_compose)

    # Deep monitoring collectors
    app.router.add_get("/api/temperature", handle_temperature)
    app.router.add_get("/api/smart", handle_smart)
    app.router.add_get("/api/tls", handle_tls)
    app.router.add_get("/api/services", handle_services)
    app.router.add_post("/api/services/{name}/restart", handle_service_restart)
    app.router.add_get("/api/crons", handle_crons)
    app.router.add_get("/api/fail2ban", handle_fail2ban)
    app.router.add_get("/api/firewall", handle_firewall)

    # Multi-server
    app.router.add_get("/api/servers", handle_servers_list)
    app.router.add_post("/api/servers", handle_server_add)
    app.router.add_get("/api/servers/overview", handle_servers_overview)
    app.router.add_get("/api/servers/{id}", handle_server_get)
    app.router.add_put("/api/servers/{id}", handle_server_update)
    app.router.add_delete("/api/servers/{id}", handle_server_delete)
    app.router.add_post("/api/servers/{id}/test", handle_server_test)
    app.router.add_post("/api/servers/{id}/probe", handle_server_probe)
    app.router.add_get("/api/servers/{id}/metrics", handle_server_metrics)

    # Notifications
    app.router.add_get("/api/notifications/channels", handle_notif_list)
    app.router.add_post("/api/notifications/channels", handle_notif_add)
    app.router.add_put("/api/notifications/channels/{id}", handle_notif_update)
    app.router.add_delete("/api/notifications/channels/{id}", handle_notif_delete)
    app.router.add_post("/api/notifications/channels/{id}/test", handle_notif_test)

    # Audit log
    app.router.add_get("/api/audit", handle_audit)

    # 2FA
    app.router.add_post("/api/auth/2fa/setup", handle_2fa_setup)
    app.router.add_post("/api/auth/2fa/verify", handle_2fa_verify)
    app.router.add_post("/api/auth/2fa/disable", handle_2fa_disable)

    # User management
    app.router.add_get("/api/users", handle_users_list)
    app.router.add_post("/api/users", handle_user_add)
    app.router.add_delete("/api/users/{id}", handle_user_delete)

    # Web Push
    app.router.add_get("/api/push/vapid-public-key", handle_push_vapid_pubkey)
    app.router.add_post("/api/push/subscribe", handle_push_subscribe)
    app.router.add_post("/api/push/unsubscribe", handle_push_unsubscribe)
    app.router.add_get("/api/push/subscriptions", handle_push_list)
    app.router.add_post("/api/push/test", handle_push_test)

    # Uptime monitoring
    app.router.add_get("/api/uptime/checks", handle_uptime_list)
    app.router.add_post("/api/uptime/checks", handle_uptime_add)
    app.router.add_put("/api/uptime/checks/{id}", handle_uptime_update)
    app.router.add_delete("/api/uptime/checks/{id}", handle_uptime_delete)
    app.router.add_get("/api/uptime/checks/{id}/history", handle_uptime_history)
    app.router.add_get("/api/uptime/checks/{id}/sla", handle_uptime_sla)

    # API tokens
    app.router.add_get("/api/tokens", handle_tokens_list)
    app.router.add_post("/api/tokens", handle_token_create)
    app.router.add_delete("/api/tokens/{id}", handle_token_revoke)

    # Forecast
    app.router.add_get("/api/forecast", handle_forecast)

    # Incidents
    app.router.add_get("/api/incidents", handle_incidents_list)
    app.router.add_get("/api/incidents/open", handle_incidents_open)
    app.router.add_get("/api/incidents/summary", handle_incidents_summary)
    app.router.add_post("/api/incidents/{id}/ack", handle_incident_ack)
    app.router.add_post("/api/incidents/{id}/postmortem", handle_incident_postmortem)

    # Public status (no auth)
    app.router.add_get("/api/status", handle_public_status)
    app.router.add_get("/status", handle_status_page)

    # Terminal + File Browser
    app.router.add_get("/ws/terminal/{server_id}", terminal.ws_terminal)
    app.router.add_get("/api/files/{server_id}/browse", terminal.ws_file_browse)
    app.router.add_get("/api/files/{server_id}/read", terminal.file_read)

    # Runbooks
    app.router.add_get("/api/runbooks", handle_runbooks_list)
    app.router.add_post("/api/runbooks", handle_runbook_create)
    app.router.add_get("/api/runbooks/{id}", handle_runbook_get)
    app.router.add_put("/api/runbooks/{id}", handle_runbook_update)
    app.router.add_delete("/api/runbooks/{id}", handle_runbook_delete)
    app.router.add_post("/api/runbooks/{id}/execute", handle_runbook_execute)
    app.router.add_get("/api/runbook-runs", handle_runbook_runs)
    app.router.add_get("/api/runbook-runs/{id}", handle_runbook_run_detail)

    # Intel: updates, backups, ssh-keys, anomaly, custom metrics
    app.router.add_get("/api/updates", handle_updates_list)
    app.router.add_post("/api/updates/scan", handle_updates_scan)
    app.router.add_get("/api/backups", handle_backups_list)
    app.router.add_post("/api/backups/scan", handle_backups_scan)
    app.router.add_get("/api/ssh-keys", handle_ssh_keys_list)
    app.router.add_post("/api/ssh-keys/scan", handle_ssh_keys_scan)
    app.router.add_get("/api/anomaly/baselines", handle_anomaly_baselines)
    app.router.add_post("/api/anomaly/scan", handle_anomaly_scan)
    app.router.add_get("/api/custom-metrics", handle_custom_metrics_list)
    app.router.add_post("/api/custom-metrics", handle_custom_metric_add)
    app.router.add_delete("/api/custom-metrics/{id}", handle_custom_metric_delete)
    app.router.add_post("/api/custom-metrics/{id}/run", handle_custom_metric_run)

    # Database monitoring
    app.router.add_get("/api/databases", handle_db_overview)
    app.router.add_get("/api/databases/{server_id}/probe", handle_db_probe)
    app.router.add_get("/api/databases/{server_id}/{db_type}", handle_db_latest)
    app.router.add_get("/api/databases/{server_id}/{db_type}/history", handle_db_history)

    # Web analytics (nginx/apache)
    app.router.add_get("/api/web-analytics", handle_web_analytics)
    app.router.add_post("/api/web-analytics/scan", handle_web_analytics_scan)

    # Docker image updates
    app.router.add_get("/api/docker-updates", handle_docker_updates)
    app.router.add_post("/api/docker-updates/scan", handle_docker_updates_scan)

    # SSL certificates (dedicated page pulling from existing TLS collector)
    app.router.add_get("/api/ssl-certs", handle_ssl_certs)

    # Prometheus metrics export (unauthenticated for scraping)
    app.router.add_get("/metrics", handle_prometheus_metrics)

    # Deploy annotations
    app.router.add_get("/api/annotations", handle_annotations_list)
    app.router.add_post("/api/annotations", handle_annotation_create)
    app.router.add_delete("/api/annotations/{id}", handle_annotation_delete)

    # Reports
    app.router.add_get("/api/reports/preview", handle_report_preview)
    app.router.add_post("/api/reports/send", handle_report_send)

    # Security audit
    app.router.add_get("/api/security-audit", handle_security_audit)
    app.router.add_post("/api/auth/force-password-change", handle_force_password_change)

    # Agent management
    app.router.add_post("/api/agent/register", handle_agent_register)
    app.router.add_post("/api/agent/heartbeat", handle_agent_heartbeat)
    app.router.add_post("/api/agent/metrics", handle_agent_metrics)
    app.router.add_post("/api/agent/logs", handle_agent_logs)
    app.router.add_get("/api/agents", handle_agents_list)
    app.router.add_get("/api/agents/{agent_id}", handle_agent_detail)
    app.router.add_delete("/api/agents/{agent_id}", handle_agent_delete)
    app.router.add_get("/api/agents/{agent_id}/metrics", handle_agent_metrics_history)
    app.router.add_get("/api/agent-logs", handle_agent_logs_search)
    app.router.add_get("/api/agent-logs/stats", handle_agent_logs_stats)
    app.router.add_get("/api/enrollment-tokens", handle_enrollment_tokens_list)
    app.router.add_post("/api/enrollment-tokens", handle_enrollment_token_create)
    app.router.add_delete("/api/enrollment-tokens/{id}", handle_enrollment_token_revoke)


# --- Auth ---

async def handle_login(request: web.Request):
    ip = request.remote
    if not auth.check_rate_limit(ip):
        return web.json_response({"error": "Rate limited. Try again later."}, status=429)

    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    user = await auth.verify_password(username, password)
    if not user:
        auth.record_attempt(ip)
        return web.json_response({"error": "Invalid credentials"}, status=401)

    token = await auth.create_session(user["id"])
    resp = web.json_response({"ok": True, "username": username})
    resp.set_cookie(
        "session", token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return resp


async def handle_logout(request: web.Request):
    token = request.cookies.get("session")
    if token:
        await auth.delete_session(token)
    resp = web.json_response({"ok": True})
    resp.del_cookie("session", path="/")
    return resp


async def handle_auth_check(request: web.Request):
    token = request.cookies.get("session")
    session = await auth.validate_session(token)
    if session:
        return web.json_response({"authenticated": True, "username": session["username"]})
    return web.json_response({"authenticated": False})


async def handle_change_password(request: web.Request):
    data = await request.json()
    new_password = data.get("password", "")
    if len(new_password) < 6:
        return web.json_response({"error": "Password must be at least 6 characters"}, status=400)
    user = request["user"]
    await auth.create_user(user["username"], new_password)
    return web.json_response({"ok": True})


# --- System ---

async def handle_system(request: web.Request):
    data = system.get_latest()
    return web.json_response(data)


async def handle_system_history(request: web.Request):
    metric = request.query.get("metric", "cpu_percent")
    duration = int(request.query.get("duration", "3600"))
    end_ts = int(time.time())
    start_ts = end_ts - duration
    result = await metrics.query_range(metric, start_ts, end_ts)
    return web.json_response(result)


# --- Docker ---

async def handle_docker(request: web.Request):
    containers = docker_mon.get_latest()
    return web.json_response({
        "available": docker_mon.is_available(),
        "containers": containers,
    })


async def handle_docker_logs(request: web.Request):
    cid = request.match_info["container_id"]
    tail = int(request.query.get("tail", "100"))
    log_text = await docker_mon.get_container_logs(cid, tail)
    return web.json_response({"logs": log_text})


async def handle_docker_action(request: web.Request):
    cid = request.match_info["container_id"]
    action = request.match_info["action"]
    if action not in ("start", "stop", "restart"):
        return web.json_response({"error": "Invalid action"}, status=400)
    result = await docker_mon.container_action(cid, action)
    return web.json_response({"result": result})


# --- Processes ---

async def handle_processes(request: web.Request):
    data = processes.get_latest()
    return web.json_response(data)


# --- Network ---

async def handle_network(request: web.Request):
    data = network.get_latest()
    return web.json_response(data)


# --- Logs ---

async def handle_logs(request: web.Request):
    n = int(request.query.get("n", "100"))
    data = logs.get_recent(n)
    return web.json_response({"logs": data})


# --- Security ---

async def handle_security(request: web.Request):
    data = security.get_recent(100)
    return web.json_response({"events": data})


async def handle_security_history(request: web.Request):
    hours = int(request.query.get("hours", "24"))
    event_type = request.query.get("type")
    data = await security.get_history(hours, event_type)
    return web.json_response({"events": data})


# --- Overview ---

async def handle_overview(request: web.Request):
    sys_data = system.get_latest()
    docker_data = docker_mon.get_latest()
    proc_data = processes.get_latest()
    net_data = network.get_latest()
    sec_data = security.get_recent(10)
    return web.json_response({
        "system": sys_data,
        "docker": {
            "available": docker_mon.is_available(),
            "containers": docker_data,
        },
        "processes": {"total": proc_data.get("total", 0)},
        "network": {
            "established": net_data.get("total_established", 0),
            "listening": net_data.get("total_listening", 0),
        },
        "security": {"recent": sec_data},
    })


# --- Alerts ---

async def handle_alerts(request: web.Request):
    active = await alerts.get_active_alerts()
    return web.json_response({"alerts": active})


async def handle_alert_ack(request: web.Request):
    alert_id = int(request.match_info["id"])
    username = request.get("user", {}).get("username", "unknown")
    await alerts.acknowledge_alert(alert_id, username)
    return web.json_response({"ok": True})


async def handle_alerts_history(request: web.Request):
    hours = int(request.query.get("hours", "24"))
    history = await alerts.get_alert_history(hours)
    return web.json_response({"alerts": history})


# --- System Info ---

async def handle_system_info(request: web.Request):
    info = system.get_system_info()
    return web.json_response(info)


# --- Metrics Export ---

async def handle_metrics_export(request: web.Request):
    metric = request.query.get("metric", "cpu_percent")
    duration = int(request.query.get("duration", "3600"))
    fmt = request.query.get("format", "json")

    if fmt not in config.METRICS_EXPORT_FORMATS:
        return web.json_response(
            {"error": f"Unsupported format. Use: {config.METRICS_EXPORT_FORMATS}"},
            status=400,
        )

    end_ts = int(time.time())
    start_ts = end_ts - duration
    result = await metrics.query_range(metric, start_ts, end_ts)

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "value", "min", "max"])
        for row in result.get("data", []):
            writer.writerow([
                row.get("ts", ""),
                row.get("value", ""),
                row.get("min_val", ""),
                row.get("max_val", ""),
            ])
        return web.Response(
            text=output.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={metric}_{duration}s.csv"},
        )

    return web.json_response(result)


# --- Health Check ---

_start_time = int(time.time())


async def handle_health(request: web.Request):
    return web.json_response({
        "status": "ok",
        "uptime": int(time.time()) - _start_time,
        "version": "1.0.0",
    })


# --- Docker Compose ---

async def handle_docker_compose(request: web.Request):
    if not docker_mon.is_available():
        return web.json_response({"available": False, "projects": {}})
    projects = docker_mon.get_compose_projects()
    return web.json_response({"available": True, "projects": projects})


# --- Temperature / Sensors ---

async def handle_temperature(request: web.Request):
    return web.json_response(temperature.get_latest())


# --- SMART ---

async def handle_smart(request: web.Request):
    return web.json_response(smart.get_latest())


# --- TLS Certificates ---

async def handle_tls(request: web.Request):
    return web.json_response(tls.get_latest())


# --- systemd Services ---

async def handle_services(request: web.Request):
    return web.json_response(services.get_latest())


async def handle_service_restart(request: web.Request):
    # Requires an authenticated user
    user = request.get("user") if hasattr(request, "get") else None
    if not user:
        return web.json_response({"error": "Authentication required"}, status=401)
    name = request.match_info.get("name", "")
    if not name:
        return web.json_response({"error": "Service name required"}, status=400)
    result = await services.restart_service(name)
    status = 200 if result.get("ok") else 500
    return web.json_response(result, status=status)


# --- Cron / Timers ---

async def handle_crons(request: web.Request):
    return web.json_response(crons.get_latest())


# --- fail2ban ---

async def handle_fail2ban(request: web.Request):
    return web.json_response(fail2ban.get_latest())


# --- Firewall ---

async def handle_firewall(request: web.Request):
    return web.json_response(firewall.get_latest())


# --- Multi-Server ---

async def handle_servers_list(request: web.Request):
    srvs = await servers.list_servers()
    status = servers.get_all_status()
    latest = servers.get_all_latest()
    # Scrub passwords
    result = []
    for s in srvs:
        item = dict(s)
        item["has_password"] = bool(item.pop("ssh_password", ""))
        item["status"] = status.get(s["id"], {"online": False, "last_seen": 0, "error": ""})
        item["metrics"] = latest.get(s["id"])
        result.append(item)
    return web.json_response({"servers": result})


async def handle_server_add(request: web.Request):
    data = await request.json()
    required = ["name", "hostname"]
    if not all(data.get(k) for k in required):
        return web.json_response({"error": "name and hostname required"}, status=400)
    try:
        sid = await servers.add_server(
            name=data["name"],
            hostname=data["hostname"],
            ssh_user=data.get("ssh_user", "root"),
            ssh_port=int(data.get("ssh_port", 22)),
            ssh_key_path=data.get("ssh_key_path", ""),
            ssh_password=data.get("ssh_password", ""),
            tags=data.get("tags", ""),
        )
        user = request.get("user", {}).get("username", "?")
        await audit.log_event(user, "server.add", f"server:{sid}", request.remote or "",
                              f"name={data['name']} host={data['hostname']}")
        return web.json_response({"ok": True, "id": sid})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_server_get(request: web.Request):
    sid = int(request.match_info["id"])
    srv = await servers.get_server(sid)
    if not srv:
        return web.json_response({"error": "not found"}, status=404)
    srv = dict(srv)
    srv["has_password"] = bool(srv.pop("ssh_password", ""))
    srv["status"] = servers.get_status(sid)
    srv["metrics"] = servers.get_latest(sid)
    return web.json_response(srv)


async def handle_server_update(request: web.Request):
    sid = int(request.match_info["id"])
    data = await request.json()
    await servers.update_server(sid, **data)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "server.update", f"server:{sid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_server_delete(request: web.Request):
    sid = int(request.match_info["id"])
    await servers.delete_server(sid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "server.delete", f"server:{sid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_server_test(request: web.Request):
    sid = int(request.match_info["id"])
    srv = await servers.get_server(sid)
    if not srv:
        return web.json_response({"error": "not found"}, status=404)
    result = await servers.test_connection(srv)
    return web.json_response(result)


async def handle_server_probe(request: web.Request):
    sid = int(request.match_info["id"])
    srv = await servers.get_server(sid)
    if not srv:
        return web.json_response({"error": "not found"}, status=404)
    await servers.probe_and_store(srv)
    return web.json_response({
        "status": servers.get_status(sid),
        "metrics": servers.get_latest(sid),
    })


async def handle_server_metrics(request: web.Request):
    sid = int(request.match_info["id"])
    metric = request.query.get("metric", "cpu_percent")
    duration = int(request.query.get("duration", "3600"))
    end_ts = int(time.time())
    start_ts = end_ts - duration
    # Query with server tag
    from .. import db as _db
    table, value_col = metrics._select_table(duration)
    if table == "metrics_raw":
        rows = await _db.execute(
            "SELECT ts, value FROM metrics_raw WHERE name = ? AND tags = ? "
            "AND ts >= ? AND ts <= ? ORDER BY ts",
            (metric, f"server={sid}", start_ts, end_ts)
        )
    else:
        rows = await _db.execute(
            f"SELECT ts, avg_val as value FROM {table} WHERE name = ? AND tags = ? "
            "AND ts >= ? AND ts <= ? ORDER BY ts",
            (metric, f"server={sid}", start_ts, end_ts)
        )
    return web.json_response({"name": metric, "data": rows})


async def handle_servers_overview(request: web.Request):
    overview = await servers.get_overview()
    return web.json_response(overview)


# --- Notifications ---

async def handle_notif_list(request: web.Request):
    channels = await notifications.list_channels()
    # Sanitize: hide sensitive fields in config
    result = []
    for c in channels:
        item = dict(c)
        try:
            cfg = json.loads(item.get("config", "{}"))
            # Mask secrets
            for key in ["webhook_url", "bot_token", "smtp_password", "headers"]:
                if key in cfg and cfg[key]:
                    cfg[key] = "***" + str(cfg[key])[-4:]
            item["config_preview"] = cfg
        except Exception:
            item["config_preview"] = {}
        item.pop("config", None)
        result.append(item)
    return web.json_response({"channels": result})


async def handle_notif_add(request: web.Request):
    data = await request.json()
    name = data.get("name")
    kind = data.get("kind")
    cfg = data.get("config", {})
    min_sev = data.get("min_severity", "warning")
    if not name or not kind:
        return web.json_response({"error": "name and kind required"}, status=400)
    cid = await notifications.add_channel(name, kind, cfg, min_sev)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "notification.add", f"channel:{cid}", request.remote or "",
                          f"name={name} kind={kind}")
    return web.json_response({"ok": True, "id": cid})


async def handle_notif_update(request: web.Request):
    cid = int(request.match_info["id"])
    data = await request.json()
    await notifications.update_channel(cid, **data)
    return web.json_response({"ok": True})


async def handle_notif_delete(request: web.Request):
    cid = int(request.match_info["id"])
    await notifications.delete_channel(cid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "notification.delete", f"channel:{cid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_notif_test(request: web.Request):
    cid = int(request.match_info["id"])
    result = await notifications.test_channel(cid)
    return web.json_response(result)


# --- Audit Log ---

async def handle_audit(request: web.Request):
    hours = int(request.query.get("hours", "24"))
    username = request.query.get("username")
    action = request.query.get("action")
    limit = int(request.query.get("limit", "500"))
    events = await audit.get_events(hours, username, action, limit)
    return web.json_response({"events": events})


# --- 2FA ---

async def handle_2fa_setup(request: web.Request):
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    secret = totp.generate_secret()
    uri = totp.otpauth_uri(secret, user["username"])
    # Store secret but don't enable yet — user needs to verify first
    from .. import db as _db
    await _db.execute(
        "UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE username = ?",
        (secret, user["username"])
    )
    return web.json_response({"secret": secret, "otpauth_uri": uri})


async def handle_2fa_verify(request: web.Request):
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    code = data.get("code", "")
    from .. import db as _db
    rows = await _db.execute(
        "SELECT totp_secret FROM users WHERE username = ?", (user["username"],)
    )
    if not rows or not rows[0].get("totp_secret"):
        return web.json_response({"error": "2FA not set up"}, status=400)
    secret = rows[0]["totp_secret"]
    if not totp.verify(secret, code):
        return web.json_response({"error": "invalid code"}, status=400)
    await _db.execute(
        "UPDATE users SET totp_enabled = 1 WHERE username = ?", (user["username"],)
    )
    await audit.log_event(user["username"], "2fa.enabled", "", request.remote or "")
    return web.json_response({"ok": True})


async def handle_2fa_disable(request: web.Request):
    user = request.get("user")
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401)
    from .. import db as _db
    await _db.execute(
        "UPDATE users SET totp_secret = '', totp_enabled = 0 WHERE username = ?",
        (user["username"],)
    )
    await audit.log_event(user["username"], "2fa.disabled", "", request.remote or "")
    return web.json_response({"ok": True})


# --- User Management ---

async def handle_users_list(request: web.Request):
    users = await auth.get_all_users()
    return web.json_response({"users": users})


async def handle_user_add(request: web.Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return web.json_response({"error": "username and password required"}, status=400)
    if len(password) < 6:
        return web.json_response({"error": "password too short"}, status=400)
    await auth.create_user(username, password)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "user.add", username, request.remote or "")
    return web.json_response({"ok": True})


async def handle_user_delete(request: web.Request):
    uid = int(request.match_info["id"])
    ok = await auth.delete_user(uid)
    if not ok:
        return web.json_response({"error": "cannot delete last user"}, status=400)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "user.delete", f"user:{uid}", request.remote or "")
    return web.json_response({"ok": True})


# --- Web Push ---

async def handle_push_vapid_pubkey(request: web.Request):
    """Public VAPID key — served to the browser for subscription."""
    key = push.get_public_key()
    if not key:
        return web.json_response({"error": "VAPID not initialized"}, status=503)
    return web.json_response({"key": key})


async def handle_push_subscribe(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "?")
    endpoint = data.get("endpoint", "")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth_token = keys.get("auth", "")
    ua = data.get("ua", "")
    if not endpoint or not p256dh or not auth_token:
        return web.json_response({"error": "missing fields"}, status=400)
    await push.subscribe(user, endpoint, p256dh, auth_token, ua)
    await audit.log_event(user, "push.subscribe", "", request.remote or "")
    return web.json_response({"ok": True})


async def handle_push_unsubscribe(request: web.Request):
    data = await request.json()
    endpoint = data.get("endpoint", "")
    await push.unsubscribe(endpoint)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "push.unsubscribe", "", request.remote or "")
    return web.json_response({"ok": True})


async def handle_push_list(request: web.Request):
    user = request.get("user", {}).get("username", None)
    subs = await push.list_subscriptions(user)
    # Don't leak auth/p256dh
    for s in subs:
        s.pop("auth", None)
        s.pop("p256dh", None)
        s["endpoint"] = s["endpoint"][:80] + "..."
    return web.json_response({"subscriptions": subs})


async def handle_push_test(request: web.Request):
    user = request.get("user", {}).get("username", None)
    n = await push.test_push(user)
    return web.json_response({"ok": True, "delivered": n})


# --- Uptime monitoring ---

async def handle_uptime_list(request: web.Request):
    checks = await uptime.list_checks()
    summary = await uptime.summary()
    return web.json_response({"checks": checks, "summary": summary})


async def handle_uptime_add(request: web.Request):
    data = await request.json()
    if not data.get("name") or not data.get("url"):
        return web.json_response({"error": "name and url required"}, status=400)
    cid = await uptime.add_check(
        name=data["name"],
        url=data["url"],
        method=data.get("method", "GET"),
        expect_status=int(data.get("expect_status", 200)),
        expect_body=data.get("expect_body", ""),
        interval_sec=int(data.get("interval_sec", 60)),
        timeout_sec=int(data.get("timeout_sec", 10)),
        headers=data.get("headers", ""),
        follow_redirects=1 if data.get("follow_redirects", True) else 0,
        tags=data.get("tags", ""),
    )
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "uptime.add", data["name"], request.remote or "")
    return web.json_response({"ok": True, "id": cid})


async def handle_uptime_update(request: web.Request):
    cid = int(request.match_info["id"])
    data = await request.json()
    await uptime.update_check(cid, data)
    return web.json_response({"ok": True})


async def handle_uptime_delete(request: web.Request):
    cid = int(request.match_info["id"])
    await uptime.delete_check(cid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "uptime.delete", f"check:{cid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_uptime_history(request: web.Request):
    cid = int(request.match_info["id"])
    hours = int(request.query.get("hours", 24))
    rows = await uptime.history(cid, hours)
    return web.json_response({"history": rows})


async def handle_uptime_sla(request: web.Request):
    cid = int(request.match_info["id"])
    return web.json_response({
        "24h": await uptime.sla(cid, 24),
        "7d":  await uptime.sla(cid, 24 * 7),
        "30d": await uptime.sla(cid, 24 * 30),
    })


# --- API tokens ---

async def handle_tokens_list(request: web.Request):
    user = request.get("user", {}).get("username", None)
    rows = await tokens.list_tokens(user)
    return web.json_response({"tokens": rows})


async def handle_token_create(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "?")
    name = data.get("name", "unnamed")
    scopes = data.get("scopes", "read")
    days = int(data.get("days", 365))
    res = await tokens.create(user, name, scopes, days)
    await audit.log_event(user, "token.create", name, request.remote or "")
    return web.json_response(res)  # Returns raw token ONCE


async def handle_token_revoke(request: web.Request):
    tid = int(request.match_info["id"])
    user = request.get("user", {}).get("username", "?")
    await tokens.revoke(tid, user)
    await audit.log_event(user, "token.revoke", f"token:{tid}", request.remote or "")
    return web.json_response({"ok": True})


# --- Forecast ---

async def handle_forecast(request: web.Request):
    metric = request.query.get("metric")
    if metric:
        target = float(request.query.get("target", 90))
        hours = int(request.query.get("hours", 72))
        return web.json_response(await forecast.forecast_metric(metric, target, hours))
    return web.json_response(await forecast.forecast_all())


# --- Incidents ---

async def handle_incidents_list(request: web.Request):
    days = int(request.query.get("days", 30))
    return web.json_response({"incidents": await incidents.list_incidents(days)})


async def handle_incidents_open(request: web.Request):
    return web.json_response({"incidents": await incidents.open_incidents()})


async def handle_incidents_summary(request: web.Request):
    days = int(request.query.get("days", 7))
    return web.json_response(await incidents.summary(days))


async def handle_incident_ack(request: web.Request):
    iid = int(request.match_info["id"])
    user = request.get("user", {}).get("username", "?")
    await incidents.ack_incident(iid, user)
    return web.json_response({"ok": True})


async def handle_incident_postmortem(request: web.Request):
    iid = int(request.match_info["id"])
    data = await request.json()
    await incidents.add_postmortem(iid, data.get("text", ""))
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "incident.postmortem", f"incident:{iid}", request.remote or "")
    return web.json_response({"ok": True})


# --- Public status (no auth) ---

async def handle_public_status(request: web.Request):
    """Unauthenticated status endpoint — safe, redacted summary."""
    try:
        srv_overview = await servers.get_overview()
    except Exception:
        srv_overview = {"total": 0, "online": 0, "offline": 0}
    try:
        up_summary = await uptime.summary()
        up_checks = await uptime.list_checks()
        # Only include essential fields (no secrets)
        public_checks = []
        for c in up_checks:
            if not c.get("enabled"):
                continue
            s = await uptime.sla(c["id"], 24)
            public_checks.append({
                "name": c["name"],
                "url": c["url"].split("?")[0],
                "up": bool(c.get("last_up")),
                "latency_ms": c.get("last_latency_ms"),
                "sla_24h": s.get("sla"),
                "last_checked": c.get("last_checked"),
            })
    except Exception:
        up_summary = {}; public_checks = []
    try:
        inc_open = await incidents.open_incidents()
        inc_summary = await incidents.summary(7)
        # Only title + severity + duration (no message to avoid info leak)
        public_incidents = [
            {"title": i["title"].split("=")[0].strip(),
             "severity": i["severity"],
             "started_at": i["started_at"]}
            for i in inc_open[:5]
        ]
    except Exception:
        inc_open = []; inc_summary = {}; public_incidents = []
    return web.json_response({
        "ok": True,
        "updated_at": int(time.time()),
        "servers": {"total": srv_overview.get("total"),
                    "online": srv_overview.get("online"),
                    "offline": srv_overview.get("offline")},
        "uptime": {"summary": up_summary, "checks": public_checks},
        "incidents": {"open": public_incidents, "summary_7d": inc_summary},
    })


async def handle_status_page(request: web.Request):
    """Serve the public status HTML page (no auth)."""
    return web.FileResponse(config.STATIC_DIR / "status.html")


# --- Runbooks ---

async def handle_runbooks_list(request: web.Request):
    rb = await runbooks.list_runbooks()
    return web.json_response({"runbooks": rb})


async def handle_runbook_get(request: web.Request):
    rid = int(request.match_info["id"])
    r = await runbooks.get_runbook(rid)
    if not r:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(r)


async def handle_runbook_create(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "?")
    if not data.get("name") or not data.get("script"):
        return web.json_response({"error": "name and script required"}, status=400)
    rid = await runbooks.create_custom(
        slug=data.get("slug", data["name"]),
        name=data["name"],
        category=data.get("category", "Custom"),
        description=data.get("description", ""),
        script=data["script"],
        dangerous=bool(data.get("dangerous", False)),
        created_by=user,
    )
    await audit.log_event(user, "runbook.create", data["name"], request.remote or "")
    return web.json_response({"ok": True, "id": rid})


async def handle_runbook_update(request: web.Request):
    rid = int(request.match_info["id"])
    data = await request.json()
    await runbooks.update_custom(rid, data)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "runbook.update", f"runbook:{rid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_runbook_delete(request: web.Request):
    rid = int(request.match_info["id"])
    await runbooks.delete_custom(rid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "runbook.delete", f"runbook:{rid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_runbook_execute(request: web.Request):
    rid = int(request.match_info["id"])
    data = await request.json()
    target = data.get("target", "local")
    dry_run = bool(data.get("dry_run", False))
    timeout = int(data.get("timeout", 120))
    user = request.get("user", {}).get("username", "?")
    result = await runbooks.execute(rid, target, triggered_by=user,
                                     dry_run=dry_run, timeout=timeout)
    await audit.log_event(
        user,
        "runbook.execute" + (".dry" if dry_run else ""),
        f"runbook:{rid}→{target}",
        request.remote or "",
        details=json.dumps({"succeeded": result.get("succeeded"), "failed": result.get("failed")}),
    )
    return web.json_response(result)


async def handle_runbook_runs(request: web.Request):
    limit = int(request.query.get("limit", 100))
    return web.json_response({"runs": await runbooks.recent_runs(limit)})


async def handle_runbook_run_detail(request: web.Request):
    rid = int(request.match_info["id"])
    r = await runbooks.get_run(rid)
    if not r:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(r)


# --- Intel: updates ---

async def handle_updates_list(request: web.Request):
    rows = await intel.list_updates()
    # Enrich with server name
    all_srv = {s["id"]: s for s in await servers.list_servers()}
    for r in rows:
        if r.get("server_id") and r["server_id"] in all_srv:
            r["server_name"] = all_srv[r["server_id"]]["name"]
        elif not r.get("server_id"):
            r["server_name"] = "localhost"
    return web.json_response({"updates": rows})


async def handle_updates_scan(request: web.Request):
    user = request.get("user", {}).get("username", "?")
    await intel.scan_all_updates()
    await audit.log_event(user, "updates.scan", "", request.remote or "")
    return web.json_response({"ok": True})


# --- Intel: backups ---

async def handle_backups_list(request: web.Request):
    rows = await intel.list_backups()
    all_srv = {s["id"]: s for s in await servers.list_servers()}
    for r in rows:
        if r.get("server_id") and r["server_id"] in all_srv:
            r["server_name"] = all_srv[r["server_id"]]["name"]
        elif not r.get("server_id"):
            r["server_name"] = "localhost"
    return web.json_response({"backups": rows})


async def handle_backups_scan(request: web.Request):
    user = request.get("user", {}).get("username", "?")
    await intel.scan_all_backups()
    await audit.log_event(user, "backups.scan", "", request.remote or "")
    return web.json_response({"ok": True})


# --- Intel: SSH keys ---

async def handle_ssh_keys_list(request: web.Request):
    rows = await intel.list_ssh_keys()
    all_srv = {s["id"]: s for s in await servers.list_servers()}
    for r in rows:
        if r.get("server_id") and r["server_id"] in all_srv:
            r["server_name"] = all_srv[r["server_id"]]["name"]
        elif not r.get("server_id"):
            r["server_name"] = "localhost"
    return web.json_response({"keys": rows})


async def handle_ssh_keys_scan(request: web.Request):
    user = request.get("user", {}).get("username", "?")
    await intel.scan_all_ssh_keys()
    await audit.log_event(user, "ssh-keys.scan", "", request.remote or "")
    return web.json_response({"ok": True})


# --- Intel: anomaly ---

async def handle_anomaly_baselines(request: web.Request):
    return web.json_response({"baselines": await intel.list_baselines()})


async def handle_anomaly_scan(request: web.Request):
    result = await intel.scan_anomalies()
    return web.json_response({"anomalies": result})


# --- Intel: custom metrics ---

async def handle_custom_metrics_list(request: web.Request):
    return web.json_response({"metrics": await intel.list_custom_metrics()})


async def handle_custom_metric_add(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "?")
    if not data.get("name") or not data.get("script"):
        return web.json_response({"error": "name and script required"}, status=400)
    mid = await intel.add_custom_metric(
        name=data["name"],
        label=data.get("label", data["name"]),
        script=data["script"],
        target=data.get("target", "local"),
        interval_sec=int(data.get("interval_sec", 300)),
        unit=data.get("unit", ""),
    )
    await audit.log_event(user, "custom-metric.add", data["name"], request.remote or "")
    return web.json_response({"ok": True, "id": mid})


async def handle_custom_metric_delete(request: web.Request):
    mid = int(request.match_info["id"])
    await intel.delete_custom_metric(mid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "custom-metric.delete", f"metric:{mid}", request.remote or "")
    return web.json_response({"ok": True})


async def handle_custom_metric_run(request: web.Request):
    mid = int(request.match_info["id"])
    rows = await intel.list_custom_metrics()
    m = next((x for x in rows if x["id"] == mid), None)
    if not m:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(await intel.run_custom_metric(m))


# --- Database monitoring ---

async def handle_db_overview(request: web.Request):
    """Get latest DB snapshots for all servers."""
    results = {}
    # Local
    results["local"] = {"snapshots": await databases.get_latest(None)}
    # Remote
    for srv in await servers.list_servers():
        if srv.get("enabled"):
            results[srv["name"]] = {
                "server_id": srv["id"],
                "snapshots": await databases.get_latest(srv["id"]),
            }
    return web.json_response(results)


async def handle_db_probe(request: web.Request):
    sid = request.match_info["server_id"]
    server_id = None if sid in ("local", "0") else int(sid)
    data = await databases.probe_all_dbs(server_id)
    return web.json_response(data)


async def handle_db_latest(request: web.Request):
    sid = request.match_info["server_id"]
    db_type = request.match_info["db_type"]
    server_id = None if sid in ("local", "0") else int(sid)
    rows = await databases.get_latest(server_id, db_type)
    return web.json_response({"snapshots": rows})


async def handle_db_history(request: web.Request):
    sid = request.match_info["server_id"]
    db_type = request.match_info["db_type"]
    hours = int(request.query.get("hours", 24))
    server_id = None if sid in ("local", "0") else int(sid)
    rows = await databases.get_history(server_id, db_type, hours)
    return web.json_response({"history": rows})


# --- Web analytics ---

async def handle_web_analytics(request: web.Request):
    sid = request.query.get("server_id")
    server_id = None if not sid or sid in ("local", "0") else int(sid)
    return web.json_response(await webanalytics.get_latest(server_id))


async def handle_web_analytics_scan(request: web.Request):
    sid = request.query.get("server_id")
    server_id = None if not sid or sid in ("local", "0") else int(sid)
    return web.json_response(await webanalytics.probe(server_id))


# --- Docker image updates ---

async def handle_docker_updates(request: web.Request):
    sid = request.query.get("server_id")
    server_id = None if not sid or sid in ("local", "0") else int(sid)
    return web.json_response(await docker_updates.get_latest(server_id))


async def handle_docker_updates_scan(request: web.Request):
    sid = request.query.get("server_id")
    server_id = None if not sid or sid in ("local", "0") else int(sid)
    return web.json_response(await docker_updates.check_images(server_id))


# --- SSL Certificates (dedicated view from existing TLS collector) ---

async def handle_ssl_certs(request: web.Request):
    """Centralized SSL cert view pulling from TLS collector cache."""
    data = tls.get_latest()
    raw_certs = data.get("certificates", []) if isinstance(data, dict) else []
    certs = []
    for info in raw_certs:
        days_left = info.get("days_remaining")
        certs.append({
            "domain": info.get("domain", ""),
            "issuer": info.get("issuer", ""),
            "subject": info.get("subject", ""),
            "not_after": info.get("expires_at", ""),
            "days_left": days_left,
            "valid": info.get("valid", False),
            "error": info.get("error", ""),
            "status": "ok" if days_left and days_left > 14 else
                      "warning" if days_left and days_left > 3 else
                      "critical" if days_left is not None else "unknown",
        })
    certs.sort(key=lambda c: c.get("days_left") or 9999)
    return web.json_response({"certs": certs, "total": len(certs)})


# --- Prometheus metrics ---

async def handle_prometheus_metrics(request: web.Request):
    """Prometheus text exposition format — unauthenticated for scraping."""
    text = prometheus.generate_metrics()
    return web.Response(text=text, content_type="text/plain")


# --- Deploy annotations ---

async def handle_annotations_list(request: web.Request):
    hours = int(request.query.get("hours", 168))
    return web.json_response({"annotations": await annotations.list_annotations(hours)})


async def handle_annotation_create(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "webhook")
    aid = await annotations.create(
        title=data.get("title", "Deploy"),
        description=data.get("description", ""),
        tags=data.get("tags", ""),
        source=data.get("source", ""),
        url=data.get("url", ""),
        created_by=user,
    )
    await audit.log_event(user, "annotation.create", data.get("title", ""), request.remote or "")
    return web.json_response({"ok": True, "id": aid})


async def handle_annotation_delete(request: web.Request):
    aid = int(request.match_info["id"])
    await annotations.delete(aid)
    return web.json_response({"ok": True})


# --- Reports ---

async def handle_report_preview(request: web.Request):
    hours = int(request.query.get("hours", 168))
    return web.json_response(await reports.get_report_preview(hours))


async def handle_report_send(request: web.Request):
    data = await request.json()
    to = data.get("to", "")
    hours = int(data.get("hours", 168))
    if not to:
        return web.json_response({"error": "email 'to' required"}, status=400)
    ok = await reports.send_report(to, hours)
    return web.json_response({"ok": ok})


# --- Security audit ---

async def handle_security_audit(request: web.Request):
    return web.json_response(await security_hardening.security_summary())


async def handle_force_password_change(request: web.Request):
    data = await request.json()
    new_password = data.get("password", "")
    user = request.get("user", {}).get("username", "")

    valid, msg = security_hardening.validate_password_strength(new_password)
    if not valid:
        return web.json_response({"error": msg}, status=400)

    # Actually change it
    import bcrypt
    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    await db.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                     (hashed.decode("utf-8"), user))
    await audit.log_event(user, "password.changed", "", request.remote or "")
    return web.json_response({"ok": True})


# --- Agent API ---

async def handle_agent_register(request: web.Request):
    """Agent enrollment — validates enrollment token, registers agent."""
    data = await request.json()
    token = data.pop("token", "")
    if not token:
        return web.json_response({"error": "enrollment token required"}, status=400)
    result = await agents.register_agent(token, data)
    if not result:
        return web.json_response({"error": "invalid or expired enrollment token"}, status=401)
    return web.json_response(result)


async def _validate_agent_request(request) -> dict:
    """Validate agent auth token from request header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        agent = await agents.validate_agent_token(token)
        if agent:
            return agent
    return None


async def handle_agent_heartbeat(request: web.Request):
    agent = await _validate_agent_request(request)
    if not agent:
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    return web.json_response(await agents.heartbeat(agent["agent_id"], data))


async def handle_agent_metrics(request: web.Request):
    agent = await _validate_agent_request(request)
    if not agent:
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    return web.json_response(await agents.ingest_metrics(
        data.get("agent_id", agent["agent_id"]),
        data.get("metrics", []),
    ))


async def handle_agent_logs(request: web.Request):
    agent = await _validate_agent_request(request)
    if not agent:
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    return web.json_response(await agents.ingest_logs(
        data.get("agent_id", agent["agent_id"]),
        data.get("logs", []),
    ))


async def handle_agents_list(request: web.Request):
    status = request.query.get("status")
    return web.json_response({"agents": await agents.list_agents(status)})


async def handle_agent_detail(request: web.Request):
    agent_id = request.match_info["agent_id"]
    agent = await agents.get_agent(agent_id)
    if not agent:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(agent)


async def handle_agent_delete(request: web.Request):
    agent_id = request.match_info["agent_id"]
    await agents.delete_agent(agent_id)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "agent.delete", agent_id, request.remote or "")
    return web.json_response({"ok": True})


async def handle_agent_metrics_history(request: web.Request):
    agent_id = request.match_info["agent_id"]
    hours = int(request.query.get("hours", 1))
    return web.json_response({"metrics": await agents.get_agent_metrics(agent_id, hours)})


async def handle_agent_logs_search(request: web.Request):
    return web.json_response({"logs": await agents.search_logs(
        agent_id=request.query.get("agent_id"),
        severity=request.query.get("severity"),
        service=request.query.get("service"),
        query=request.query.get("q"),
        hours=int(request.query.get("hours", 24)),
        limit=int(request.query.get("limit", 500)),
    )})


async def handle_agent_logs_stats(request: web.Request):
    hours = int(request.query.get("hours", 24))
    return web.json_response(await agents.log_stats(hours))


async def handle_enrollment_tokens_list(request: web.Request):
    return web.json_response({"tokens": await agents.list_enrollment_tokens()})


async def handle_enrollment_token_create(request: web.Request):
    data = await request.json()
    user = request.get("user", {}).get("username", "?")
    result = await agents.create_enrollment_token(
        name=data.get("name", ""),
        created_by=user,
        expires_hours=int(data.get("expires_hours", 24)),
        max_uses=int(data.get("max_uses", 0)),
        tags=data.get("tags", ""),
    )
    await audit.log_event(user, "enrollment.create", data.get("name", ""), request.remote or "")
    return web.json_response(result)


async def handle_enrollment_token_revoke(request: web.Request):
    tid = int(request.match_info["id"])
    await agents.revoke_enrollment_token(tid)
    user = request.get("user", {}).get("username", "?")
    await audit.log_event(user, "enrollment.revoke", f"token:{tid}", request.remote or "")
    return web.json_response({"ok": True})
