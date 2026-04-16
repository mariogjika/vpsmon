"""Nginx/Apache access log analytics — real-time request stats.

Parses access logs via SSH, extracts request rate, status codes,
top endpoints, response times, error rates.
"""
import asyncio
import json
import logging
import time
from typing import Optional

from . import db, servers

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

NGINX_ANALYTICS_SCRIPT = r"""
python3 -c "
import json, re, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

logs = []
# Find nginx access log
for path in ['/var/log/nginx/access.log', '/var/log/apache2/access.log', '/var/log/httpd/access_log']:
    if os.path.exists(path):
        logs.append(path)
# Also check docker nginx
for path in ['/var/lib/docker/containers']:
    pass  # Skip docker for now

if not logs:
    print(json.dumps({'error': 'no access log found'}))
    sys.exit(0)

# Parse last 10000 lines of first log
import subprocess
raw = subprocess.run(['tail', '-n', '10000', logs[0]], capture_output=True, text=True, timeout=10).stdout

# Combined log format regex
pat = re.compile(r'(\S+) \S+ \S+ \[([^\]]+)\] \"(\S+) (\S+) [^\"]*\" (\d{3}) (\d+|-) \"([^\"]*)\" \"([^\"]*)\"')
now = datetime.now()
cutoff_1h = now - timedelta(hours=1)
cutoff_24h = now - timedelta(hours=24)

total = 0; total_1h = 0; total_24h = 0
status_codes = Counter()
endpoints = Counter()
methods = Counter()
ips = Counter()
errors = []
sizes = []
hourly_buckets = defaultdict(int)

for line in raw.splitlines():
    m = pat.match(line)
    if not m:
        continue
    ip, ts_str, method, path, status, size, referer, ua = m.groups()
    total += 1
    status = int(status)
    size = int(size) if size != '-' else 0
    status_codes[status] += 1
    endpoints[path.split('?')[0]] += 1
    methods[method] += 1
    ips[ip] += 1
    sizes.append(size)

    # Parse timestamp
    try:
        ts = datetime.strptime(ts_str.split()[0], '%d/%b/%Y:%H:%M:%S')
        if ts > cutoff_1h: total_1h += 1
        if ts > cutoff_24h:
            total_24h += 1
            hourly_buckets[ts.strftime('%Y-%m-%d %H:00')] += 1
        if status >= 400:
            errors.append({'ts': ts_str, 'method': method, 'path': path[:80], 'status': status, 'ip': ip})
    except: pass

avg_size = round(sum(sizes)/len(sizes)) if sizes else 0
error_rate = round(sum(1 for s in status_codes if s >= 400) / max(total, 1) * 100, 2)

result = {
    'log_file': logs[0],
    'total_requests': total,
    'requests_1h': total_1h,
    'requests_24h': total_24h,
    'rpm_1h': round(total_1h / 60, 1),
    'avg_response_size': avg_size,
    'error_rate_pct': error_rate,
    'status_codes': dict(status_codes.most_common(20)),
    'top_endpoints': dict(endpoints.most_common(15)),
    'top_ips': dict(ips.most_common(10)),
    'methods': dict(methods),
    'recent_errors': errors[-20:][::-1],
    'hourly': dict(sorted(hourly_buckets.items())[-24:]),
    'unique_ips_24h': len(set(ip for ip, _ in ips.most_common())),
}
print(json.dumps(result))
" 2>/dev/null || echo '{"error":"analytics script failed"}'
"""


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS web_analytics (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id   INTEGER,
        ts          INTEGER NOT NULL,
        data        TEXT NOT NULL
    )""")


async def probe(server_id: Optional[int] = None) -> dict:
    from .runbooks import _execute_local, _execute_remote
    if server_id is None:
        r = await _execute_local(NGINX_ANALYTICS_SCRIPT, timeout=30)
    else:
        srv = await servers.get_server(server_id)
        if not srv:
            return {"error": "server not found"}
        r = await _execute_remote(srv, NGINX_ANALYTICS_SCRIPT, timeout=30)

    text = r.get("output", "").strip()
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                data = json.loads(line)
                await _ensure_schema()
                await db.execute(
                    "INSERT INTO web_analytics (server_id, ts, data) VALUES (?,?,?)",
                    (server_id, int(time.time()), json.dumps(data)),
                )
                return data
            except json.JSONDecodeError:
                pass
    return {"error": "no output", "raw": text[:300]}


async def get_latest(server_id: Optional[int] = None) -> dict:
    await _ensure_schema()
    rows = await db.execute(
        "SELECT data FROM web_analytics WHERE server_id IS ? ORDER BY ts DESC LIMIT 1",
        (server_id,),
    )
    if not rows:
        return {"error": "no data yet — run a scan first"}
    try:
        return json.loads(rows[0]["data"])
    except Exception:
        return {"error": "corrupt data"}


async def _loop():
    await _ensure_schema()
    while True:
        try:
            await probe(None)
            for srv in await servers.list_servers():
                if srv.get("enabled", 1):
                    try:
                        await probe(srv["id"])
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Web analytics loop: {e}")
        # Prune old data (keep 7 days)
        cutoff = int(time.time()) - 7 * 86400
        try:
            await db.execute("DELETE FROM web_analytics WHERE ts < ?", (cutoff,))
        except Exception:
            pass
        await asyncio.sleep(300)  # Every 5 min


async def start():
    global _task
    _task = asyncio.create_task(_loop())
    log.info("Web analytics started")


async def stop():
    if _task:
        _task.cancel()
