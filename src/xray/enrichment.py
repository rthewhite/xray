"""Connection enrichment for xray firewall notifications.

Enriches firewall prompts with DNS domain names and guest process info
by running a script on the guest VM via SSH. All enrichment is best-effort
with graceful fallback to raw IP/port display.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from . import config, ssh


@dataclass
class EnrichmentResult:
    """Result of enriching a connection with DNS and process info."""
    domain: str | None = None
    process_name: str | None = None
    process_pid: str | None = None


@dataclass
class ConnectionRecord:
    """Record of a recent firewall decision."""
    timestamp: float
    dest_ip: str
    dest_port: int
    domain: str | None = None
    process_name: str | None = None
    decision: str = ""


# Per-VM DNS cache: vm_name -> {ip -> domain}
_dns_cache: dict[str, dict[str, str]] = {}
_dns_cache_lock = threading.Lock()

# Per-VM recent connection records: vm_name -> deque of ConnectionRecord
_recent_connections: dict[str, deque[ConnectionRecord]] = {}
_recent_lock = threading.Lock()


def enrich(vm_name: str, dest_ip: str, dest_port: int) -> EnrichmentResult:
    """Enrich a connection with domain name and process info from the guest.

    Makes a single SSH call to run /usr/local/bin/xray-enrich on the guest.
    Results are cached (DNS mappings) to avoid repeated SSH calls.

    Args:
        vm_name: VM name (to look up SSH port and cached data)
        dest_ip: Destination IP address
        dest_port: Destination port

    Returns:
        EnrichmentResult with whatever info was available. Empty on any error.
    """
    result = EnrichmentResult()

    # Check DNS cache first
    with _dns_cache_lock:
        vm_cache = _dns_cache.get(vm_name, {})
        cached_domain = vm_cache.get(dest_ip)
        if cached_domain:
            result.domain = cached_domain

    # If we have a cached domain and don't need process info urgently,
    # we could skip SSH. But process info changes per connection, so
    # always try SSH (it's fast when the script exists).
    vm_cfg = config.read_vm_config(vm_name)
    ssh_port = vm_cfg.get("ssh_port")
    ssh_user = vm_cfg.get("ssh_user", "ubuntu")

    if not ssh_port:
        return result

    try:
        rc, stdout, stderr = ssh.run_command(
            "127.0.0.1",
            ssh_port,
            f"/usr/local/bin/xray-enrich {dest_ip} {dest_port}",
            user=ssh_user,
            timeout=5,
        )

        if stderr.strip():
            print(f"[enrich] xray-enrich debug: {stderr.strip()}")

        if rc != 0 and not stdout.strip():
            print(f"[enrich] xray-enrich failed (rc={rc})")
            return result

        print(f"[enrich] xray-enrich output: {stdout.strip()!r}")

        # Parse key=value output
        for line in stdout.strip().splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key == "domain" and value:
                result.domain = value
            elif key == "process_name" and value:
                result.process_name = value
            elif key == "process_pid" and value:
                result.process_pid = value

        # Cache DNS result
        if result.domain:
            with _dns_cache_lock:
                if vm_name not in _dns_cache:
                    _dns_cache[vm_name] = {}
                _dns_cache[vm_name][dest_ip] = result.domain

    except Exception as e:
        print(f"[enrich] error: {e}")

    return result


def record_connection(
    vm_name: str,
    dest_ip: str,
    dest_port: int,
    domain: str | None,
    process_name: str | None,
    decision: str,
) -> None:
    """Record a firewall decision for recent connection display."""
    record = ConnectionRecord(
        timestamp=time.time(),
        dest_ip=dest_ip,
        dest_port=dest_port,
        domain=domain,
        process_name=process_name,
        decision=decision,
    )
    with _recent_lock:
        if vm_name not in _recent_connections:
            _recent_connections[vm_name] = deque(maxlen=20)
        _recent_connections[vm_name].append(record)


def get_recent_connections(vm_name: str, limit: int = 5) -> list[ConnectionRecord]:
    """Get the most recent firewall decisions for a VM."""
    with _recent_lock:
        records = _recent_connections.get(vm_name, deque())
        # Return the last `limit` entries (most recent)
        return list(records)[-limit:]


def clear_vm_state(vm_name: str) -> None:
    """Clear all cached state for a VM (call on shutdown)."""
    with _dns_cache_lock:
        _dns_cache.pop(vm_name, None)
    with _recent_lock:
        _recent_connections.pop(vm_name, None)
