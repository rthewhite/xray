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
