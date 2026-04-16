import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from . import config

_executor = ThreadPoolExecutor(max_workers=2)
_db_path: Path = config.DB_PATH
_thread_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8192")       # 8 MB
    conn.execute("PRAGMA mmap_size=268435456")     # 256 MB
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _conn() -> sqlite3.Connection:
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        _thread_local.conn = _get_conn()
    return _thread_local.conn


async def execute(sql: str, params: tuple = ()) -> list:
    def _run():
        c = _conn()
        cur = c.execute(sql, params)
        c.commit()
        return [dict(r) for r in cur.fetchall()]
    return await asyncio.get_event_loop().run_in_executor(_executor, _run)


async def execute_many(sql: str, params_list: list) -> None:
    def _run():
        c = _conn()
        c.executemany(sql, params_list)
        c.commit()
    await asyncio.get_event_loop().run_in_executor(_executor, _run)


async def execute_script(sql: str) -> None:
    def _run():
        c = _conn()
        c.executescript(sql)
    await asyncio.get_event_loop().run_in_executor(_executor, _run)


SCHEMA = """
-- Raw metrics: 5-second resolution, 24h retention
CREATE TABLE IF NOT EXISTS metrics_raw (
    ts      INTEGER NOT NULL,
    name    TEXT NOT NULL,
    value   REAL NOT NULL,
    tags    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_raw_ts ON metrics_raw(ts);
CREATE INDEX IF NOT EXISTS idx_raw_name_ts ON metrics_raw(name, ts);

-- 1-minute aggregates: 7-day retention
CREATE TABLE IF NOT EXISTS metrics_1m (
    ts      INTEGER NOT NULL,
    name    TEXT NOT NULL,
    avg_val REAL NOT NULL,
    min_val REAL NOT NULL,
    max_val REAL NOT NULL,
    count   INTEGER NOT NULL,
    tags    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_1m_ts ON metrics_1m(ts);
CREATE INDEX IF NOT EXISTS idx_1m_name_ts ON metrics_1m(name, ts);

-- 5-minute aggregates: 30-day retention
CREATE TABLE IF NOT EXISTS metrics_5m (
    ts      INTEGER NOT NULL,
    name    TEXT NOT NULL,
    avg_val REAL NOT NULL,
    min_val REAL NOT NULL,
    max_val REAL NOT NULL,
    count   INTEGER NOT NULL,
    tags    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_5m_ts ON metrics_5m(ts);
CREATE INDEX IF NOT EXISTS idx_5m_name_ts ON metrics_5m(name, ts);

-- 1-hour aggregates: kept forever
CREATE TABLE IF NOT EXISTS metrics_1h (
    ts      INTEGER NOT NULL,
    name    TEXT NOT NULL,
    avg_val REAL NOT NULL,
    min_val REAL NOT NULL,
    max_val REAL NOT NULL,
    count   INTEGER NOT NULL,
    tags    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_1h_ts ON metrics_1h(ts);
CREATE INDEX IF NOT EXISTS idx_1h_name_ts ON metrics_1h(name, ts);

-- Security events
CREATE TABLE IF NOT EXISTS security_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info',
    source_ip   TEXT DEFAULT '',
    username    TEXT DEFAULT '',
    message     TEXT NOT NULL,
    raw_log     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sec_ts ON security_events(ts);
CREATE INDEX IF NOT EXISTS idx_sec_type ON security_events(event_type, ts);

-- Docker events
CREATE TABLE IF NOT EXISTS docker_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    container   TEXT NOT NULL,
    action      TEXT NOT NULL,
    message     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_docker_ts ON docker_events(ts);

-- Multi-server: remote servers to monitor
CREATE TABLE IF NOT EXISTS servers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    hostname       TEXT NOT NULL,
    ssh_user       TEXT DEFAULT 'root',
    ssh_port       INTEGER DEFAULT 22,
    ssh_key_path   TEXT DEFAULT '',
    ssh_password   TEXT DEFAULT '',
    tags           TEXT DEFAULT '',
    enabled        INTEGER DEFAULT 1,
    created_at     INTEGER NOT NULL
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    username  TEXT NOT NULL,
    action    TEXT NOT NULL,
    resource  TEXT DEFAULT '',
    ip        TEXT DEFAULT '',
    details   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(username, ts);

-- Notification channels
CREATE TABLE IF NOT EXISTS notification_channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    kind       TEXT NOT NULL,
    config     TEXT NOT NULL,
    enabled    INTEGER DEFAULT 1,
    min_severity TEXT DEFAULT 'warning',
    created_at INTEGER NOT NULL
);

-- Auth: users
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    role        TEXT DEFAULT 'admin',
    totp_secret TEXT DEFAULT '',
    totp_enabled INTEGER DEFAULT 0,
    created_at  INTEGER NOT NULL
);

-- Auth: sessions
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_sess_expires ON sessions(expires_at);

-- Alerts
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    alert_type      TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'warning',
    metric_name     TEXT NOT NULL,
    threshold       REAL,
    current_value   REAL,
    message         TEXT NOT NULL,
    acknowledged    INTEGER DEFAULT 0,
    ack_by          TEXT DEFAULT '',
    ack_at          INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged, ts);

-- System info snapshots
CREATE TABLE IF NOT EXISTS system_info (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


async def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await execute_script(SCHEMA)
    # Migrations for existing DBs — ADD COLUMN is idempotent-safe when wrapped
    for alter in [
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin'",
        "ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0",
    ]:
        try:
            await execute(alter)
        except Exception:
            pass  # Column already exists


async def vacuum():
    await execute("VACUUM")
