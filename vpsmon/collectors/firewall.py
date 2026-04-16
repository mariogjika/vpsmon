import asyncio
import logging
import re
import shutil

log = logging.getLogger(__name__)

_latest = {"available": False, "backend": None, "rules": []}


def get_latest() -> dict:
    return dict(_latest)


async def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return -1, "", "timeout"
        return proc.returncode or 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as e:
        return -1, "", str(e)


# UFW numbered rule: [ 1] 22/tcp                     ALLOW IN    Anywhere
_UFW_RULE = re.compile(
    r"^\s*\[\s*(\d+)\s*\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)\s+(IN|OUT|FWD)?\s*(.*)$",
    re.IGNORECASE,
)


def _parse_ufw(text: str) -> tuple[bool, list[dict]]:
    active = False
    rules = []
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("status:"):
            active = "active" in low
            continue
        m = _UFW_RULE.match(line)
        if m:
            rules.append({
                "num": int(m.group(1)),
                "to": m.group(2).strip(),
                "action": m.group(3).upper(),
                "direction": (m.group(4) or "").upper(),
                "from": m.group(5).strip(),
            })
    return active, rules


async def _collect_ufw() -> tuple[bool, list[dict], bool]:
    """Returns (found_ufw, rules, active)."""
    if not shutil.which("ufw"):
        return False, [], False
    rc, out, _ = await _run(["ufw", "status", "numbered"])
    if rc != 0 or not out:
        return False, [], False
    active, rules = _parse_ufw(out)
    return True, rules, active


async def _collect_iptables() -> tuple[bool, list[dict]]:
    if not shutil.which("iptables"):
        return False, []
    rc, out, _ = await _run(["iptables", "-L", "-n", "--line-numbers"])
    if rc != 0 or not out:
        return False, []
    rules = []
    current_chain = None
    header_seen = False
    for line in out.splitlines():
        s = line.rstrip()
        if not s:
            current_chain = None
            header_seen = False
            continue
        if s.startswith("Chain "):
            # Chain INPUT (policy ACCEPT)
            parts = s.split()
            current_chain = parts[1] if len(parts) > 1 else None
            header_seen = False
            continue
        if s.lstrip().startswith("num") or s.lstrip().startswith("target"):
            header_seen = True
            continue
        if current_chain is None:
            continue
        parts = s.split(None, 6)
        if not parts:
            continue
        # With --line-numbers, first token is num
        try:
            num = int(parts[0])
            target = parts[1] if len(parts) > 1 else ""
            proto = parts[2] if len(parts) > 2 else ""
            opt = parts[3] if len(parts) > 3 else ""
            source = parts[4] if len(parts) > 4 else ""
            destination = parts[5] if len(parts) > 5 else ""
            extra = parts[6] if len(parts) > 6 else ""
        except ValueError:
            continue
        rules.append({
            "chain": current_chain,
            "num": num,
            "target": target,
            "proto": proto,
            "opt": opt,
            "source": source,
            "destination": destination,
            "extra": extra,
        })
    return True, rules


async def collect_once() -> dict:
    result = {"available": False, "backend": None, "rules": [], "active": False}

    found_ufw, ufw_rules, ufw_active = await _collect_ufw()
    if found_ufw:
        result["available"] = True
        result["backend"] = "ufw"
        result["rules"] = ufw_rules
        result["active"] = ufw_active
    else:
        found_ipt, ipt_rules = await _collect_iptables()
        if found_ipt:
            result["available"] = True
            result["backend"] = "iptables"
            result["rules"] = ipt_rules
            result["active"] = True

    _latest.clear()
    _latest.update(result)
    return result
