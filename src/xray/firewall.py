"""Firewall setup and guest configuration for xray VMs."""

from __future__ import annotations

import threading
from pathlib import Path

from . import config, enrichment, notifier

# The proxy is exposed to guests via QEMU guestfwd
# Can't use 10.0.2.2 (gateway) - QEMU reserves it, use 10.0.2.100 instead
GUEST_PROXY_IP = "10.0.2.100"
# We use a fixed port so the guest knows where to connect
GUEST_PROXY_PORT = 1080

# Default config file name
DEFAULT_RULES_FILE = "default-firewall-rules.conf"

# Built-in defaults (used if config file doesn't exist)
BUILTIN_DEFAULT_DOMAINS = """# Default allowed domains for xray firewall
# Lines starting with # are comments
# Each line should be a domain suffix to allow (e.g., "github.com" allows *.github.com)

# Ubuntu package repositories
archive.ubuntu.com
ports.ubuntu.com
security.ubuntu.com
ppa.launchpad.net
ppa.launchpadcontent.net

# Canonical services (NTP, mirrors, etc.)
canonical.com
ubuntu.com
launchpad.net

# Common package sources
debian.org
deb.nodesource.com
dl.google.com
packages.microsoft.com
download.docker.com

# Development services
github.com
githubusercontent.com
pypi.org
files.pythonhosted.org
npmjs.org
registry.npmjs.org
"""


def _get_default_rules_path() -> Path:
    """Get path to the default firewall rules config file."""
    return config.xray_home() / DEFAULT_RULES_FILE


def _ensure_default_rules_file() -> Path:
    """Ensure the default rules file exists, creating it with defaults if not."""
    path = _get_default_rules_path()
    if not path.exists():
        path.write_text(BUILTIN_DEFAULT_DOMAINS)
    return path


def _read_default_domains() -> list[str]:
    """Read default allowed domains from config file."""
    path = _ensure_default_rules_file()
    domains: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if line and not line.startswith("#"):
            domains.append(line.lower())
    return domains


def get_default_allowed_domains() -> dict[str, str]:
    """Get default allowed domains from config file.

    Returns a dict of domain patterns to allow.
    """
    domains = _read_default_domains()
    return {domain: "allow" for domain in domains}


def get_ssh_port(name: str) -> int | None:
    """Get the SSH port for a VM."""
    vm_cfg = config.read_vm_config(name)
    return vm_cfg.get("ssh_port")


# Lock to serialize firewall notifications (one at a time)
_notification_lock = threading.Lock()


def _matches_default_domain(hostname: str) -> str | None:
    """Check if hostname matches any default allowed domain.

    Returns the matched domain pattern, or None.
    """
    default_domains = get_default_allowed_domains()
    if not default_domains:
        return None
    hostname_lower = hostname.lower()
    for domain in default_domains:
        if hostname_lower == domain or hostname_lower.endswith("." + domain):
            return domain
    return None


def check_rule(vm_name: str, dest_ip: str, dest_port: int) -> str | None:
    """Check if a connection is allowed/denied by firewall rules.

    This function BLOCKS until the user responds to the notification.
    Multiple connections to the same IP:port will queue up and wait.

    Returns:
        "allow" if explicitly allowed
        "deny" if explicitly denied or user denies
    """
    rule_key = f"{dest_ip}:{dest_port}"

    verbose = config.is_verbose()

    # Fast path: check if rule already exists (no lock needed for read)
    rules = config.read_firewall_rules(vm_name)
    if rule_key in rules:
        decision = rules[rule_key]
        if verbose:
            print(f"[firewall] {rule_key} -> {decision} (existing rule)")
        enrichment.record_connection(vm_name, dest_ip, dest_port, None, None, decision)
        return decision

    # Enrich the connection with domain/process info from the guest's
    # dnsmasq log. This tells us which domain resolved to this IP, which
    # reverse DNS cannot reliably do (CDN/cloud IPs return provider names).
    if verbose:
        print(f"[firewall] {rule_key} -> enriching...")
    info = enrichment.enrich(vm_name, dest_ip, dest_port)

    # Check if the enriched domain matches a default allowed domain
    if info.domain:
        match = _matches_default_domain(info.domain)
        if match:
            if verbose:
                print(f"[firewall] {dest_ip} ({info.domain}) -> auto-allowed (matches default: {match})")
            print(f"[firewall] {rule_key} allowed ({info.domain})")
            config.add_firewall_rule(vm_name, dest_ip, dest_port, "allow")
            enrichment.record_connection(vm_name, dest_ip, dest_port, info.domain, None, "allow")
            return "allow"

    # Fallback: try reverse DNS (works for IPs with correct PTR records)
    hostname = notifier._get_hostname(dest_ip)
    if hostname:
        match = _matches_default_domain(hostname)
        if match:
            if verbose:
                print(f"[firewall] {dest_ip} ({hostname}) -> auto-allowed (matches default: {match})")
            print(f"[firewall] {rule_key} allowed ({hostname})")
            config.add_firewall_rule(vm_name, dest_ip, dest_port, "allow")
            enrichment.record_connection(vm_name, dest_ip, dest_port, hostname, None, "allow")
            return "allow"

    # No rule exists - need to prompt user
    # Use lock to serialize notifications (one dialog at a time)
    if verbose:
        print(f"[firewall] {rule_key} -> no rule, waiting for lock...")
    with _notification_lock:
        # Re-check rules in case another thread added it while we waited
        rules = config.read_firewall_rules(vm_name)
        if rule_key in rules:
            decision = rules[rule_key]
            if verbose:
                print(f"[firewall] {rule_key} -> {decision} (added while waiting)")
            enrichment.record_connection(vm_name, dest_ip, dest_port, None, None, decision)
            return decision

        recent = enrichment.get_recent_connections(vm_name)

        # Show notification and WAIT for response
        if verbose:
            print(f"[firewall] {rule_key} -> showing notification...")
        decision = notifier.show_firewall_alert(
            vm_name, dest_ip, dest_port,
            domain=info.domain,
            process_name=info.process_name,
            recent_connections=recent,
        )

        # Store the decision for future connections
        config.add_firewall_rule(vm_name, dest_ip, dest_port, decision)
        domain_label = info.domain or hostname or ""
        if domain_label:
            print(f"[firewall] {rule_key} {decision} (user: {domain_label})")
        else:
            print(f"[firewall] {rule_key} {decision} (user)")
        if verbose:
            print(f"[firewall] {rule_key} -> user chose: {decision}")

        enrichment.record_connection(
            vm_name, dest_ip, dest_port,
            info.domain, info.process_name, decision,
        )

        return decision
