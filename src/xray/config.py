"""Configuration and path management for xray."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib
import tomli_w


def xray_home() -> Path:
    """Return the xray home directory, creating it if needed."""
    home = Path(os.environ.get("XRAY_HOME", Path.home() / ".xray"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def bases_dir() -> Path:
    d = xray_home() / "bases"
    d.mkdir(exist_ok=True)
    return d


def vms_dir() -> Path:
    d = xray_home() / "vms"
    d.mkdir(exist_ok=True)
    return d


def claude_creds_dir() -> Path:
    """Get the path to xray's managed Claude credentials directory."""
    creds_dir = xray_home() / ".claude"
    creds_dir.mkdir(parents=True, exist_ok=True)
    return creds_dir


def plugins_dir() -> Path:
    """Get path to the plugins directory."""
    return xray_home() / "plugins"


def read_firewall_rules(name: str) -> dict[str, str]:
    """Read firewall rules from VM config.

    Returns:
        dict mapping "IP:PORT" -> "allow" or "deny"
        Example: {"1.1.1.1:443": "allow", "10.0.0.1:22": "deny"}
    """
    vm_cfg = read_vm_config(name)
    return vm_cfg.get("firewall", {})


def add_firewall_rule(name: str, ip: str, port: int, action: str) -> None:
    """Add a firewall rule to VM config.

    Args:
        name: VM name
        ip: Destination IP address
        port: Destination port
        action: "allow" or "deny"
    """
    if action not in ("allow", "deny"):
        raise ValueError(f"Action must be 'allow' or 'deny', got: {action}")

    vm_cfg = read_vm_config(name)
    firewall = vm_cfg.get("firewall", {})
    rule_key = f"{ip}:{port}"
    firewall[rule_key] = action
    vm_cfg["firewall"] = firewall
    write_vm_config(name, vm_cfg)


def vm_dir(name: str) -> Path:
    return vms_dir() / name


def vm_config_path(name: str) -> Path:
    return vm_dir(name) / "vm.toml"


def vm_disk_path(name: str) -> Path:
    return vm_dir(name) / "disk.qcow2"


def vm_pid_path(name: str) -> Path:
    return vm_dir(name) / "pid"


def vm_qmp_path(name: str) -> Path:
    return vm_dir(name) / "qmp.sock"


def vm_efivars_path(name: str) -> Path:
    return vm_dir(name) / "efivars.fd"


def read_vm_config(name: str) -> dict:
    path = vm_config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"VM '{name}' not found")
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_vm_config(name: str, config: dict) -> None:
    path = vm_config_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(config, f)


def list_vms() -> list[str]:
    d = vms_dir()
    if not d.exists():
        return []
    return sorted(
        entry.name for entry in d.iterdir()
        if entry.is_dir() and (entry / "vm.toml").exists()
    )


def list_bases() -> list[str]:
    d = bases_dir()
    if not d.exists():
        return []
    return sorted(
        entry.stem for entry in d.iterdir()
        if entry.suffix == ".qcow2"
    )


def get_used_ssh_ports() -> set[int]:
    """Get all SSH ports currently assigned to VMs."""
    used_ports: set[int] = set()
    for vm_name in list_vms():
        try:
            vm_cfg = read_vm_config(vm_name)
            if "ssh_port" in vm_cfg:
                used_ports.add(vm_cfg["ssh_port"])
        except FileNotFoundError:
            pass
    return used_ports


def next_available_ssh_port(start: int = 2222) -> int:
    """Find the next available SSH port starting from `start`."""
    used = get_used_ssh_ports()
    port = start
    while port in used:
        port += 1
    return port
