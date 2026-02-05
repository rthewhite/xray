"""Base image management."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import config


def import_base(source: Path, name: str | None = None, link: bool = False) -> str:
    """Import a qcow2 base image by copying or symlinking it."""
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(f"Source image not found: {source}")
    if source.suffix != ".qcow2":
        raise ValueError(f"Expected a .qcow2 file, got: {source.name}")

    if name is None:
        name = source.stem

    dest = config.bases_dir() / f"{name}.qcow2"
    if dest.exists():
        raise FileExistsError(f"Base image '{name}' already exists")

    if link:
        dest.symlink_to(source)
    else:
        shutil.copy2(source, dest)

    return name


def remove_base(name: str) -> None:
    """Remove a base image."""
    path = config.bases_dir() / f"{name}.qcow2"
    if not path.exists():
        raise FileNotFoundError(f"Base image '{name}' not found")

    # Check if any VM uses this base
    for vm_name in config.list_vms():
        vm_cfg = config.read_vm_config(vm_name)
        if vm_cfg.get("base") == name:
            raise RuntimeError(
                f"Cannot remove: VM '{vm_name}' uses base image '{name}'"
            )

    path.unlink()


def get_base_path(name: str) -> Path:
    """Get the path to a base image, raising if not found."""
    path = config.bases_dir() / f"{name}.qcow2"
    if not path.exists():
        raise FileNotFoundError(f"Base image '{name}' not found")
    return path


def base_info(name: str) -> dict:
    """Get info about a base image."""
    path = get_base_path(name)
    stat = path.stat()
    return {
        "name": name,
        "path": str(path),
        "size": stat.st_size,
        "is_link": path.is_symlink(),
    }
