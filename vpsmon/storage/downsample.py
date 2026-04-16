import asyncio
import logging
import time

from .. import db, config

log = logging.getLogger(__name__)

_running = False


async def start():
    global _running
    _running = True
    asyncio.create_task(_downsample_loop())
    asyncio.create_task(_purge_loop())
    log.info("Downsampling pipeline started")


async def stop():
    global _running
    _running = False


async def _downsample_loop():
    while _running:
        try:
            await _downsample_raw_to_1m()
            await asyncio.sleep(config.DOWNSAMPLE_1M_INTERVAL)
            if not _running:
                break
            await _downsample_1m_to_5m()
            await asyncio.sleep(config.DOWNSAMPLE_5M_INTERVAL - config.DOWNSAMPLE_1M_INTERVAL)
            if not _running:
                break
            await _downsample_5m_to_1h()
        except Exception as e:
            log.error(f"Downsample error: {e}")
        await asyncio.sleep(60)


async def _purge_loop():
    while _running:
        await asyncio.sleep(config.PURGE_INTERVAL)
        if not _running:
            break
        try:
            await _purge_old_data()
        except Exception as e:
            log.error(f"Purge error: {e}")


async def _downsample_raw_to_1m():
    now = int(time.time())
    # Aggregate raw data older than 2 minutes into 1m buckets
    cutoff = now - 120
    await db.execute(
        """INSERT INTO metrics_1m (ts, name, avg_val, min_val, max_val, count, tags)
           SELECT (ts / 60) * 60, name, AVG(value), MIN(value), MAX(value), COUNT(*), tags
           FROM metrics_raw
           WHERE ts < ?
           AND ts > (SELECT COALESCE(MAX(ts), 0) FROM metrics_1m) - 60
           GROUP BY (ts / 60) * 60, name, tags""",
        (cutoff,)
    )


async def _downsample_1m_to_5m():
    now = int(time.time())
    cutoff = now - 600
    await db.execute(
        """INSERT INTO metrics_5m (ts, name, avg_val, min_val, max_val, count, tags)
           SELECT (ts / 300) * 300, name,
                  SUM(avg_val * count) / SUM(count),
                  MIN(min_val), MAX(max_val), SUM(count), tags
           FROM metrics_1m
           WHERE ts < ?
           AND ts > (SELECT COALESCE(MAX(ts), 0) FROM metrics_5m) - 300
           GROUP BY (ts / 300) * 300, name, tags""",
        (cutoff,)
    )


async def _downsample_5m_to_1h():
    now = int(time.time())
    cutoff = now - 7200
    await db.execute(
        """INSERT INTO metrics_1h (ts, name, avg_val, min_val, max_val, count, tags)
           SELECT (ts / 3600) * 3600, name,
                  SUM(avg_val * count) / SUM(count),
                  MIN(min_val), MAX(max_val), SUM(count), tags
           FROM metrics_5m
           WHERE ts < ?
           AND ts > (SELECT COALESCE(MAX(ts), 0) FROM metrics_1h) - 3600
           GROUP BY (ts / 3600) * 3600, name, tags""",
        (cutoff,)
    )


async def _purge_old_data():
    now = int(time.time())
    batch = 10000

    # Purge raw (24h)
    cutoff = now - config.RAW_RETENTION
    await db.execute(
        f"DELETE FROM metrics_raw WHERE rowid IN (SELECT rowid FROM metrics_raw WHERE ts < ? LIMIT {batch})",
        (cutoff,)
    )

    # Purge 1m (7 days)
    cutoff = now - config.ONE_MIN_RETENTION
    await db.execute(
        f"DELETE FROM metrics_1m WHERE rowid IN (SELECT rowid FROM metrics_1m WHERE ts < ? LIMIT {batch})",
        (cutoff,)
    )

    # Purge 5m (30 days)
    cutoff = now - config.FIVE_MIN_RETENTION
    await db.execute(
        f"DELETE FROM metrics_5m WHERE rowid IN (SELECT rowid FROM metrics_5m WHERE ts < ? LIMIT {batch})",
        (cutoff,)
    )

    # Purge old sessions
    await db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))

    # Purge old security events (keep 90 days)
    cutoff = now - (90 * 86400)
    await db.execute(
        f"DELETE FROM security_events WHERE rowid IN (SELECT rowid FROM security_events WHERE ts < ? LIMIT {batch})",
        (cutoff,)
    )

    log.debug("Purge cycle complete")
