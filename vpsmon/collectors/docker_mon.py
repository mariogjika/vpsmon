import asyncio
import logging
import time
from collections import defaultdict

log = logging.getLogger(__name__)

_client = None
_available = False
_latest_containers = []


def is_available() -> bool:
    return _available


def get_latest() -> list:
    return list(_latest_containers)


async def init():
    global _client, _available
    try:
        import docker
        loop = asyncio.get_event_loop()
        _client = await loop.run_in_executor(None, docker.from_env)
        await loop.run_in_executor(None, _client.ping)
        _available = True
        log.info("Docker monitoring enabled")
    except Exception as e:
        _available = False
        log.info(f"Docker not available: {e}")


async def collect_once() -> list:
    if not _available or not _client:
        return []
    loop = asyncio.get_event_loop()
    try:
        containers = await loop.run_in_executor(None, _collect_sync)
        _latest_containers.clear()
        _latest_containers.extend(containers)
        return containers
    except Exception as e:
        log.warning(f"Docker collection error: {e}")
        return _latest_containers


def _collect_sync() -> list:
    containers = _client.containers.list(all=True)
    results = []
    for c in containers:
        labels = c.labels or {}
        info = {
            "id": c.short_id,
            "name": c.name,
            "image": str(c.image.tags[0]) if c.image.tags else str(c.image.short_id),
            "status": c.status,
            "state": c.attrs.get("State", {}).get("Status", "unknown"),
            "created": c.attrs.get("Created", ""),
            "compose_project": labels.get("com.docker.compose.project", ""),
            "compose_service": labels.get("com.docker.compose.service", ""),
        }
        if c.status == "running":
            try:
                stats = c.stats(stream=False)
                info.update(_parse_stats(stats))
            except Exception:
                pass
        results.append(info)
    return results


def _parse_stats(stats: dict) -> dict:
    result = {}

    # CPU
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - \
                precpu.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - \
                   precpu.get("system_cpu_usage", 0)
    num_cpus = cpu.get("online_cpus", 1)
    if system_delta > 0 and cpu_delta >= 0:
        result["cpu_percent"] = (cpu_delta / system_delta) * num_cpus * 100
    else:
        result["cpu_percent"] = 0.0

    # Memory
    mem = stats.get("memory_stats", {})
    result["mem_usage"] = mem.get("usage", 0)
    result["mem_limit"] = mem.get("limit", 0)
    if result["mem_limit"] > 0:
        result["mem_percent"] = (result["mem_usage"] / result["mem_limit"]) * 100
    else:
        result["mem_percent"] = 0.0

    # Network
    nets = stats.get("networks", {})
    rx = sum(n.get("rx_bytes", 0) for n in nets.values())
    tx = sum(n.get("tx_bytes", 0) for n in nets.values())
    result["net_rx"] = rx
    result["net_tx"] = tx

    # Block IO
    bio = stats.get("blkio_stats", {}).get("io_service_bytes_recursive", []) or []
    result["block_read"] = sum(e.get("value", 0) for e in bio if e.get("op") == "read")
    result["block_write"] = sum(e.get("value", 0) for e in bio if e.get("op") == "write")

    # PIDs
    result["pids"] = stats.get("pids_stats", {}).get("current", 0)

    return result


async def get_container_logs(container_id: str, tail: int = 100) -> str:
    if not _available or not _client:
        return ""
    loop = asyncio.get_event_loop()
    try:
        def _get():
            c = _client.containers.get(container_id)
            return c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
        return await loop.run_in_executor(None, _get)
    except Exception as e:
        return f"Error: {e}"


async def container_action(container_id: str, action: str) -> str:
    if not _available or not _client:
        return "Docker not available"
    loop = asyncio.get_event_loop()
    try:
        def _do():
            c = _client.containers.get(container_id)
            getattr(c, action)()
            return f"Container {container_id} {action} successful"
        return await loop.run_in_executor(None, _do)
    except Exception as e:
        return f"Error: {e}"


def get_compose_projects() -> dict:
    """Group containers by their docker-compose project label.

    Returns a dict of project_name -> list of containers.
    Containers without a compose project are grouped under '_standalone'.
    """
    containers = get_latest()
    projects: dict[str, list] = defaultdict(list)
    for c in containers:
        project = c.get("compose_project", "") or "_standalone"
        projects[project].append(c)
    return dict(projects)
