"""Resource forecasting — simple linear regression over recent samples."""
import time
from typing import Optional

from . import db


async def _recent_samples(metric: str, hours: int = 72) -> list:
    """Return [(ts, value), ...] for the given metric from metrics tables."""
    cutoff = int(time.time()) - hours * 3600
    # Prefer 5m tier for smooth trend; fall back to 1m
    for table in ("metrics_5m", "metrics_1m", "metrics_raw"):
        try:
            rows = await db.execute(
                f"SELECT ts, value FROM {table} WHERE metric = ? AND ts > ? ORDER BY ts ASC",
                (metric, cutoff),
            )
            if len(rows) >= 10:
                return [(r["ts"], r["value"]) for r in rows]
        except Exception:
            continue
    return []


def _linreg(points: list) -> Optional[tuple]:
    """Return (slope, intercept) for y = mx + b. Returns None if insufficient data."""
    n = len(points)
    if n < 5:
        return None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


async def forecast_metric(metric: str, target: float, hours_lookback: int = 72) -> dict:
    """Predict when `metric` will reach `target` based on recent trend.

    Returns:
      {
        current: float, slope_per_day: float,
        hits_target_at: int|None (unix ts),
        days_until: float|None,
        confidence: 'low'|'medium'|'high'
      }
    """
    points = await _recent_samples(metric, hours_lookback)
    if not points:
        return {"current": None, "error": "no data"}
    current = points[-1][1]
    r = _linreg(points)
    if not r:
        return {"current": current, "error": "insufficient data"}
    slope, intercept = r  # per second
    # Confidence: compute R² (coefficient of determination)
    mean_y = sum(p[1] for p in points) / len(points)
    ss_tot = sum((p[1] - mean_y) ** 2 for p in points) or 1e-9
    ss_res = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in points)
    r2 = max(0.0, 1 - ss_res / ss_tot)
    confidence = "high" if r2 > 0.7 else ("medium" if r2 > 0.3 else "low")
    slope_per_day = slope * 86400
    result = {
        "current": round(current, 3),
        "slope_per_day": round(slope_per_day, 4),
        "r_squared": round(r2, 3),
        "confidence": confidence,
        "target": target,
    }
    if slope > 0 and current < target:
        seconds_until = (target - current) / slope
        if seconds_until > 0:
            result["hits_target_at"] = int(time.time()) + int(seconds_until)
            result["days_until"] = round(seconds_until / 86400, 1)
    elif slope < 0 and current > target:
        seconds_until = (current - target) / abs(slope)
        result["hits_target_at"] = int(time.time()) + int(seconds_until)
        result["days_until"] = round(seconds_until / 86400, 1)
    else:
        result["hits_target_at"] = None
        result["days_until"] = None
    return result


async def forecast_all() -> dict:
    """Standard fleet forecasts: disk-full, memory-saturation."""
    out = {}
    # Disk full at 95%
    out["disk_full"] = await forecast_metric("disk_percent", 95.0, hours_lookback=168)
    # Memory saturation at 90%
    out["memory_saturation"] = await forecast_metric("memory_percent", 90.0, hours_lookback=72)
    # Swap usage reaching 50%
    out["swap_saturation"] = await forecast_metric("swap_percent", 50.0, hours_lookback=72)
    return out
