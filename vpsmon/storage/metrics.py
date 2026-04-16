import time
from .. import db, config


async def insert_raw(rows: list[tuple]):
    if not rows:
        return
    await db.execute_many(
        "INSERT INTO metrics_raw (ts, name, value, tags) VALUES (?,?,?,?)",
        rows
    )


async def query(name: str, start_ts: int, end_ts: int = None) -> list:
    if end_ts is None:
        end_ts = int(time.time())
    duration = end_ts - start_ts
    table, value_col = _select_table(duration)
    rows = await db.execute(
        f"SELECT ts, {value_col} as value FROM {table} "
        f"WHERE name = ? AND ts >= ? AND ts <= ? ORDER BY ts",
        (name, start_ts, end_ts)
    )
    return rows


async def query_latest(names: list[str], duration: int = 3600) -> dict:
    now = int(time.time())
    start = now - duration
    result = {}
    for name in names:
        rows = await query(name, start, now)
        result[name] = rows
    return result


def _select_table(duration_seconds: int) -> tuple[str, str]:
    if duration_seconds <= 1800:       # 30 min -> raw
        return "metrics_raw", "value"
    elif duration_seconds <= 21600:    # 6 hours -> 1m
        return "metrics_1m", "avg_val"
    elif duration_seconds <= 259200:   # 3 days -> 5m
        return "metrics_5m", "avg_val"
    else:                              # longer -> 1h
        return "metrics_1h", "avg_val"


async def query_range(name: str, start_ts: int, end_ts: int) -> dict:
    """Query with min/max for aggregated tiers."""
    duration = end_ts - start_ts
    table, _ = _select_table(duration)
    if table == "metrics_raw":
        rows = await db.execute(
            "SELECT ts, value, value as min_val, value as max_val FROM metrics_raw "
            "WHERE name = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (name, start_ts, end_ts)
        )
    else:
        rows = await db.execute(
            f"SELECT ts, avg_val as value, min_val, max_val FROM {table} "
            "WHERE name = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (name, start_ts, end_ts)
        )
    return {"name": name, "data": rows}
