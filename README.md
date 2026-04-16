# VPSMon — Open-Source Server Monitoring

**The free, self-hosted monitoring dashboard for VPS and virtual machine fleets.**

Built by [Solverix](https://solverix.io) — deploy in 30 seconds, monitor everything, zero agents required.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)

---

## What is VPSMon?

VPSMon is a lightweight (~35MB RAM), self-hosted server monitoring dashboard that runs directly on your VPS. It provides real-time and historical observability for system metrics, Docker containers, databases, web traffic, and security events — all through a clean, modern web interface.

**Key differentiators:**
- **Agentless multi-server** — monitor your entire fleet via SSH from one dashboard, no agents to install
- **SSH Web Terminal** — full interactive shell in the browser (xterm.js powered)
- **Visual File Browser** — navigate remote filesystems with point-and-click
- **18 built-in Runbooks** — one-click automation across your fleet
- **Database monitoring** — PostgreSQL, MySQL, Redis stats auto-detected
- **90+ day retention** — 4-tier time-series downsampling in SQLite (~100-300MB steady state)
- **Installable PWA** — works offline, push notifications to your phone/desktop

## Quick Start

### One-line install (Ubuntu/Debian)

```bash
curl -sL https://raw.githubusercontent.com/solverix/vpsmon/main/install.sh | sudo bash
```

### Docker

```bash
docker compose up -d
```

### Manual

```bash
git clone https://github.com/solverix/vpsmon.git
cd vpsmon
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m vpsmon.app
```

Open `http://your-server-ip:9090` — default login: `admin` / `admin` (change immediately!)

## Features

### Monitoring
- **System metrics** — CPU (per-core), memory, swap, disk I/O, network TX/RX, load averages
- **Docker containers** — live stats, start/stop/restart, log viewer, compose group detection
- **Processes** — top by CPU/memory, sortable, with kill capability
- **Network** — active connections, listening ports

### Databases
- **PostgreSQL** — connections, cache hit ratio, slow queries, replication lag, deadlocks
- **MySQL** — connections, queries/sec, InnoDB buffer hit ratio, slow queries
- **Redis** — clients, memory, hit ratio, keys, evicted keys

### Web & SSL
- **Nginx/Apache analytics** — request rate, top endpoints, status codes, error log, top IPs
- **SSL Certificate dashboard** — centralized expiry tracking with countdown alerts

### Fleet Management
- **Multi-server monitoring** — agentless SSH probing, add unlimited servers
- **Uptime checks** — synthetic HTTP monitoring with SLA tracking (24h/7d/30d)
- **Package updates** — pending apt/dnf updates + security update counts per server
- **Backup inventory** — auto-detects restic, borg, rsync, cron backups

### Operations
- **SSH Web Terminal** — full interactive shell in the browser via xterm.js
- **Visual File Browser** — navigate and read files on any fleet server
- **Runbooks** — 18 built-in + custom scripts, run on local/remote/all/by-tag, dry-run mode
- **Custom Metrics** — define shell scripts that record numbers as first-class metrics

### Alerting & Incidents
- **Alert system** — threshold-based + anomaly detection (3σ from baseline)
- **Incident timeline** — auto-created from alerts, MTTR tracking, postmortem notes
- **Notifications** — Slack, Discord, Telegram, Email (SMTP), Webhook, Web Push
- **Deploy annotations** — mark releases on your timeline via webhook

### Security
- **Security audit page** — score + grade with actionable recommendations
- **2FA (TOTP)** — Google Authenticator / Authy / 1Password compatible
- **SSH key inventory** — audit authorized_keys across every server
- **Fail2ban status** — jails + currently-banned IPs
- **Audit log** — who did what, when, from where

### Developer Experience
- **Command palette** — `Cmd-K` / `Ctrl-K` for quick navigation + actions
- **Prometheus /metrics** — plug into Grafana with zero config
- **API tokens** — scoped (read/full), long-lived tokens for scripts
- **Public status page** — `/status` shareable with clients, no auth required
- **PWA** — installable on desktop/phone, offline-capable
- **Dark/Light mode** — toggle in sidebar footer

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.9+ / aiohttp |
| Frontend | Vanilla JS + Canvas charts |
| Storage | SQLite (WAL mode) |
| Terminal | xterm.js + asyncssh |
| Auth | bcrypt + TOTP + sessions |
| Push | VAPID Web Push |

**Total dependencies:** `aiohttp`, `psutil`, `docker`, `bcrypt`, `pywebpush`, `asyncssh` — that's it.

## Data Retention

| Tier | Resolution | Retention | Purpose |
|------|-----------|-----------|---------|
| Raw | 5 seconds | 24 hours | Real-time debugging |
| 1 min | 1 minute | 7 days | Short-term trends |
| 5 min | 5 minutes | 30 days | Medium-term analysis |
| 1 hour | 1 hour | 90+ days | Capacity planning |

## Resource Usage

- **RAM:** ~35MB RSS (256MB hard cap via systemd)
- **CPU:** <3% average
- **Disk:** ~100-300MB at steady state
- **Network:** minimal (WebSocket + SSH probes)

## API

Every feature is API-accessible. Use session cookies or `Authorization: Bearer <token>` headers.

```bash
# Create an API token
curl -X POST https://your-server/api/tokens \
  -H "Authorization: Bearer <session>" \
  -d '{"name":"ci-deploy","scopes":"full","days":365}'

# Mark a deploy
curl -X POST https://your-server/api/annotations \
  -H "Authorization: Bearer vpsm_xxx" \
  -d '{"title":"Deployed v2.3","source":"github-actions"}'

# Prometheus metrics
curl https://your-server/metrics
```

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

## License

MIT License — free for personal and commercial use.

---

**Built with care by [Solverix](https://solverix.io)** — Innovative digital transformation through data-driven insights.
