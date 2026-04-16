"""Database monitoring — PostgreSQL, MySQL, Redis stats via SSH probes.

Agentless: runs diagnostic SQL/commands over SSH, parses JSON output.
Works on local server and across the fleet.
"""
import asyncio
import json
import logging
import time
from typing import Optional

from . import db, servers

log = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

# ==================== Probe Scripts ====================

POSTGRES_PROBE = r"""
su - postgres -c "psql -t -A -F'|' -c \"
SELECT json_build_object(
  'version', version(),
  'uptime_seconds', extract(epoch from now() - pg_postmaster_start_time())::int,
  'connections', (SELECT count(*) FROM pg_stat_activity),
  'max_connections', current_setting('max_connections')::int,
  'active_queries', (SELECT count(*) FROM pg_stat_activity WHERE state='active'),
  'idle_connections', (SELECT count(*) FROM pg_stat_activity WHERE state='idle'),
  'waiting', (SELECT count(*) FROM pg_stat_activity WHERE wait_event_type IS NOT NULL AND state='active'),
  'db_size_bytes', (SELECT sum(pg_database_size(datname)) FROM pg_stat_database WHERE datname NOT IN ('template0','template1')),
  'transactions_committed', (SELECT sum(xact_commit) FROM pg_stat_database),
  'transactions_rolled_back', (SELECT sum(xact_rollback) FROM pg_stat_database),
  'cache_hit_ratio', ROUND((SELECT sum(blks_hit)::numeric / NULLIF(sum(blks_hit) + sum(blks_read), 0) * 100 FROM pg_stat_database), 2),
  'temp_files', (SELECT sum(temp_files) FROM pg_stat_database),
  'temp_bytes', (SELECT sum(temp_bytes) FROM pg_stat_database),
  'deadlocks', (SELECT sum(deadlocks) FROM pg_stat_database),
  'conflicts', (SELECT sum(conflicts) FROM pg_stat_database),
  'databases', (SELECT json_agg(json_build_object('name',datname,'size',pg_database_size(datname),'conns',(SELECT count(*) FROM pg_stat_activity WHERE datname=d.datname))) FROM pg_stat_database d WHERE datname NOT IN ('template0','template1')),
  'replication_lag', (SELECT COALESCE(extract(epoch from replay_lag)::int, 0) FROM pg_stat_replication LIMIT 1),
  'slow_queries', (SELECT json_agg(json_build_object('query',left(query,120),'duration',round(extract(epoch from now()-query_start)::numeric,1),'state',state,'user',usename)) FROM pg_stat_activity WHERE state='active' AND query NOT LIKE '%pg_stat%' AND extract(epoch from now()-query_start) > 5 LIMIT 5)
)\"" 2>/dev/null || echo '{"error":"postgres not available"}'
"""

MYSQL_PROBE = r"""
mysql -u root -N -e "
SELECT JSON_OBJECT(
  'version', VERSION(),
  'uptime_seconds', VARIABLE_VALUE
) FROM performance_schema.global_status WHERE VARIABLE_NAME='Uptime';
" 2>/dev/null && mysql -u root -N -e "
SELECT JSON_OBJECT(
  'connections', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Threads_connected'),
  'max_connections', (SELECT VARIABLE_VALUE FROM performance_schema.global_variables WHERE VARIABLE_NAME='max_connections'),
  'active_queries', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Threads_running'),
  'questions', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Questions'),
  'slow_queries', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Slow_queries'),
  'bytes_received', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Bytes_received'),
  'bytes_sent', (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Bytes_sent'),
  'innodb_buffer_pool_hit_ratio', ROUND(
    (1 - (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Innodb_buffer_pool_reads') /
    NULLIF((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Innodb_buffer_pool_read_requests'), 0)) * 100, 2),
  'db_size_bytes', (SELECT SUM(data_length + index_length) FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','performance_schema','mysql','sys'))
);
" 2>/dev/null || echo '{"error":"mysql not available"}'
"""

REDIS_PROBE = r"""
redis-cli INFO 2>/dev/null | python3 -c "
import sys, json
data = {}
for line in sys.stdin:
    line = line.strip()
    if ':' in line and not line.startswith('#'):
        k, v = line.split(':', 1)
        try: data[k] = int(v)
        except:
            try: data[k] = float(v)
            except: data[k] = v
out = {
    'version': data.get('redis_version', ''),
    'uptime_seconds': data.get('uptime_in_seconds', 0),
    'connected_clients': data.get('connected_clients', 0),
    'used_memory_bytes': data.get('used_memory', 0),
    'used_memory_peak_bytes': data.get('used_memory_peak', 0),
    'total_connections': data.get('total_connections_received', 0),
    'total_commands': data.get('total_commands_processed', 0),
    'keyspace_hits': data.get('keyspace_hits', 0),
    'keyspace_misses': data.get('keyspace_misses', 0),
    'hit_ratio': round(data.get('keyspace_hits',0) / max(data.get('keyspace_hits',0)+data.get('keyspace_misses',0), 1) * 100, 2),
    'evicted_keys': data.get('evicted_keys', 0),
    'blocked_clients': data.get('blocked_clients', 0),
    'db_count': sum(1 for k in data if k.startswith('db')),
    'total_keys': sum(int(str(data.get(k,'')).split(',')[0].split('=')[1]) for k in data if k.startswith('db') and 'keys=' in str(data.get(k,''))),
    'rdb_last_save_time': data.get('rdb_last_save_time', 0),
    'connected_slaves': data.get('connected_slaves', 0),
}
print(json.dumps(out))
" 2>/dev/null || echo '{"error":"redis not available"}'
"""

DB_DETECT_SCRIPT = r"""
echo '{'
echo '"postgres":'
command -v psql >/dev/null 2>&1 && su - postgres -c "psql -t -c 'SELECT 1'" >/dev/null 2>&1 && echo 'true' || echo 'false'
echo ',"mysql":'
command -v mysql >/dev/null 2>&1 && mysql -u root -e "SELECT 1" >/dev/null 2>&1 && echo 'true' || echo 'false'
echo ',"redis":'
command -v redis-cli >/dev/null 2>&1 && redis-cli PING >/dev/null 2>&1 && echo 'true' || echo 'false'
echo '}'
"""


# ==================== Storage ====================

async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS db_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id   INTEGER,
        db_type     TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        data        TEXT NOT NULL
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_db_snap ON db_snapshots(server_id, db_type, ts DESC)")


async def _store(server_id: Optional[int], db_type: str, data: dict):
    now = int(time.time())
    await db.execute(
        "INSERT INTO db_snapshots (server_id, db_type, ts, data) VALUES (?,?,?,?)",
        (server_id, db_type, now, json.dumps(data)),
    )
    # Keep 7 days
    cutoff = now - 7 * 86400
    await db.execute("DELETE FROM db_snapshots WHERE ts < ?", (cutoff,))


# ==================== Probe Logic ====================

async def _run_probe(server_id: Optional[int], script: str) -> dict:
    """Run a probe script locally or on a remote server."""
    from .runbooks import _execute_local, _execute_remote
    if server_id is None:
        r = await _execute_local(script, timeout=30)
    else:
        srv = await servers.get_server(server_id)
        if not srv:
            return {"error": "server not found"}
        r = await _execute_remote(srv, script, timeout=30)

    text = r.get("output", "").strip()
    # Find last JSON line
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"error": "no JSON output", "raw": text[:500]}


async def detect_databases(server_id: Optional[int] = None) -> dict:
    return await _run_probe(server_id, DB_DETECT_SCRIPT)


async def probe_postgres(server_id: Optional[int] = None) -> dict:
    data = await _run_probe(server_id, POSTGRES_PROBE)
    if "error" not in data:
        await _store(server_id, "postgres", data)
    return data


async def probe_mysql(server_id: Optional[int] = None) -> dict:
    data = await _run_probe(server_id, MYSQL_PROBE)
    if "error" not in data:
        await _store(server_id, "mysql", data)
    return data


async def probe_redis(server_id: Optional[int] = None) -> dict:
    data = await _run_probe(server_id, REDIS_PROBE)
    if "error" not in data:
        await _store(server_id, "redis", data)
    return data


async def probe_all_dbs(server_id: Optional[int] = None) -> dict:
    """Auto-detect and probe all databases on a server."""
    detected = await detect_databases(server_id)
    results = {"detected": detected}
    if detected.get("postgres"):
        results["postgres"] = await probe_postgres(server_id)
    if detected.get("mysql"):
        results["mysql"] = await probe_mysql(server_id)
    if detected.get("redis"):
        results["redis"] = await probe_redis(server_id)
    return results


async def get_latest(server_id: Optional[int] = None, db_type: Optional[str] = None) -> list:
    """Get latest snapshots."""
    await _ensure_schema()
    if db_type:
        rows = await db.execute(
            "SELECT * FROM db_snapshots WHERE server_id IS ? AND db_type = ? ORDER BY ts DESC LIMIT 1",
            (server_id, db_type),
        )
    else:
        # Latest per type
        rows = await db.execute(
            """SELECT * FROM db_snapshots WHERE server_id IS ?
               AND ts = (SELECT MAX(ts) FROM db_snapshots d2
                         WHERE d2.server_id IS db_snapshots.server_id AND d2.db_type = db_snapshots.db_type)
               ORDER BY db_type""",
            (server_id,),
        )
    for r in rows:
        try:
            r["parsed"] = json.loads(r["data"])
        except Exception:
            r["parsed"] = {}
    return rows


async def get_history(server_id: Optional[int], db_type: str, hours: int = 24) -> list:
    cutoff = int(time.time()) - hours * 3600
    rows = await db.execute(
        "SELECT ts, data FROM db_snapshots WHERE server_id IS ? AND db_type = ? AND ts > ? ORDER BY ts ASC",
        (server_id, db_type, cutoff),
    )
    for r in rows:
        try:
            r["parsed"] = json.loads(r["data"])
        except Exception:
            r["parsed"] = {}
    return rows


# ==================== Background Loop ====================

async def _db_loop():
    """Probe all databases on local + all remote servers every 60s."""
    await _ensure_schema()
    while True:
        try:
            # Local
            await probe_all_dbs(None)
            # Remote servers
            for srv in await servers.list_servers():
                if not srv.get("enabled", 1):
                    continue
                try:
                    await probe_all_dbs(srv["id"])
                except Exception as e:
                    log.debug(f"DB probe {srv['name']}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"DB probe loop error: {e}")
        await asyncio.sleep(60)


async def start():
    global _task
    await _ensure_schema()
    _task = asyncio.create_task(_db_loop())
    log.info("Database monitoring started")


async def stop():
    global _task
    if _task:
        _task.cancel()
