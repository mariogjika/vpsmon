"""Server comparison — overlay metrics from 2 servers side-by-side."""
import json
import time
from typing import Optional

from . import db, agents, servers


async def compare_overview(server_a, server_b) -> dict:
    """Return latest snapshot for both servers for a quick health table."""
    results = {}
    for label, sid in [("a", server_a), ("b", server_b)]:
        # Try agent metrics first
        agent_rows = await db.execute(
            "SELECT data FROM agent_metrics WHERE agent_id = ? ORDER BY ts DESC LIMIT 20",
            (str(sid),),
        )
        if agent_rows:
            metrics = {}
            for r in agent_rows:
                try:
                    d = json.loads(r["data"])
                    m = d.get("metric", "")
                    if m and m not in metrics:
                        metrics[m] = d.get("value")
                except Exception:
                    pass
            results[label] = {"source": "agent", "metrics": metrics}
        else:
            # Try SSH probe cache from servers module
            try:
                from . import servers as srv_mod
                overview = await srv_mod.get_overview()
                srv_data = {}
                for s in overview.get("servers", []):
                    if str(s.get("id")) == str(sid) or s.get("name") == str(sid):
                        srv_data = s.get("metrics") or {}
                        break
                results[label] = {"source": "ssh", "metrics": srv_data}
            except Exception:
                results[label] = {"source": "none", "metrics": {}}
    return results


async def compare_metrics(server_a, server_b, metric: str = "cpu_percent",
                          hours: int = 1) -> dict:
    """Query time-series for a specific metric from both servers."""
    cutoff = int(time.time()) - hours * 3600
    result = {"metric": metric, "hours": hours}

    for label, sid in [("a", server_a), ("b", server_b)]:
        rows = await db.execute(
            "SELECT ts, data FROM agent_metrics WHERE agent_id = ? AND ts > ? ORDER BY ts ASC",
            (str(sid), cutoff),
        )
        points = []
        for r in rows:
            try:
                d = json.loads(r["data"])
                if d.get("metric") == metric:
                    points.append({"ts": r["ts"], "value": d.get("value", 0)})
            except Exception:
                pass
        result[label] = {"server": str(sid), "points": points, "count": len(points)}

    return result


async def available_metrics(server_id) -> list:
    """List all distinct metric names available for a server."""
    rows = await db.execute(
        "SELECT DISTINCT data FROM agent_metrics WHERE agent_id = ? ORDER BY ts DESC LIMIT 200",
        (str(server_id),),
    )
    names = set()
    for r in rows:
        try:
            d = json.loads(r["data"])
            if d.get("metric"):
                names.add(d["metric"])
        except Exception:
            pass
    return sorted(names)
