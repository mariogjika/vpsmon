"""Docker image update checker — compares running images against registry latest."""
import asyncio
import json
import logging
import time
from typing import Optional

from . import db

log = logging.getLogger(__name__)

DOCKER_IMAGE_CHECK_SCRIPT = r"""
python3 -c "
import subprocess, json, sys

# Get running container images
result = subprocess.run(['docker', 'ps', '--format', '{{.Image}}|{{.Names}}|{{.ID}}'],
                       capture_output=True, text=True, timeout=10)
if result.returncode != 0:
    print(json.dumps({'error': 'docker not available'}))
    sys.exit(0)

images = []
seen = set()
for line in result.stdout.strip().splitlines():
    parts = line.split('|')
    if len(parts) >= 2:
        img, name = parts[0], parts[1]
        if img not in seen:
            seen.add(img)
            # Get local image digest
            inspect = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}|{{.Created}}|{{index .RepoDigests 0}}',
                                     img], capture_output=True, text=True, timeout=5)
            local_id = ''
            created = ''
            digest = ''
            if inspect.returncode == 0:
                iparts = inspect.stdout.strip().split('|')
                local_id = iparts[0][:19] if len(iparts) > 0 else ''
                created = iparts[1][:19] if len(iparts) > 1 else ''
                digest = iparts[2] if len(iparts) > 2 else ''

            # Check if update available (pull --dry-run style)
            # We just compare local digest vs registry
            has_update = False
            try:
                pull_check = subprocess.run(['docker', 'pull', '--quiet', img],
                                           capture_output=True, text=True, timeout=30)
                if pull_check.returncode == 0:
                    # Re-inspect to see if digest changed
                    new_inspect = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}', img],
                                               capture_output=True, text=True, timeout=5)
                    if new_inspect.returncode == 0:
                        new_id = new_inspect.stdout.strip()[:19]
                        has_update = new_id != local_id and local_id != ''
            except: pass

            images.append({
                'image': img,
                'container': name,
                'local_id': local_id,
                'created': created,
                'has_update': has_update,
            })

print(json.dumps({'images': images, 'total': len(images), 'updatable': sum(1 for i in images if i['has_update'])}))
" 2>/dev/null || echo '{"error":"check failed"}'
"""


async def _ensure_schema():
    await db.execute("""
    CREATE TABLE IF NOT EXISTS docker_image_checks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id   INTEGER,
        ts          INTEGER NOT NULL,
        data        TEXT NOT NULL
    )""")


async def check_images(server_id: Optional[int] = None) -> dict:
    from .runbooks import _execute_local, _execute_remote
    if server_id is None:
        r = await _execute_local(DOCKER_IMAGE_CHECK_SCRIPT, timeout=120)
    else:
        from . import servers as srv_mod
        srv = await srv_mod.get_server(server_id)
        if not srv:
            return {"error": "server not found"}
        r = await _execute_remote(srv, DOCKER_IMAGE_CHECK_SCRIPT, timeout=120)

    text = r.get("output", "").strip()
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                data = json.loads(line)
                await _ensure_schema()
                await db.execute(
                    "INSERT INTO docker_image_checks (server_id, ts, data) VALUES (?,?,?)",
                    (server_id, int(time.time()), json.dumps(data)),
                )
                return data
            except json.JSONDecodeError:
                pass
    return {"error": "no output"}


async def get_latest(server_id: Optional[int] = None) -> dict:
    await _ensure_schema()
    rows = await db.execute(
        "SELECT data, ts FROM docker_image_checks WHERE server_id IS ? ORDER BY ts DESC LIMIT 1",
        (server_id,),
    )
    if not rows:
        return {"error": "no check data — run a scan first"}
    try:
        d = json.loads(rows[0]["data"])
        d["checked_at"] = rows[0]["ts"]
        return d
    except Exception:
        return {"error": "corrupt data"}
