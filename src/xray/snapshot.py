"""Snapshot management — routes to qemu-img or QMP depending on VM state."""

from __future__ import annotations

from . import config, qemu, vm as vm_mod
from .qmp import QMPClient, QMPError


def create(vm_name: str, snap_name: str) -> None:
    """Create a snapshot. Uses QMP if VM is running, qemu-img otherwise."""
    _ensure_vm_exists(vm_name)
    disk = config.vm_disk_path(vm_name)

    if vm_mod.is_running(vm_name):
        with QMPClient(config.vm_qmp_path(vm_name)) as qmp:
            result = qmp.savevm(snap_name)
            if result:  # non-empty means error message
                raise RuntimeError(f"Snapshot failed: {result}")
    else:
        qemu.snapshot_create(disk, snap_name)


def list_snapshots(vm_name: str) -> str:
    """List snapshots. Uses QMP if VM is running, qemu-img otherwise."""
    _ensure_vm_exists(vm_name)
    disk = config.vm_disk_path(vm_name)

    if vm_mod.is_running(vm_name):
        with QMPClient(config.vm_qmp_path(vm_name)) as qmp:
            return qmp.info_snapshots()
    else:
        return qemu.snapshot_list(disk)


def revert(vm_name: str, snap_name: str) -> None:
    """Revert to a snapshot. Uses QMP if running, qemu-img otherwise."""
    _ensure_vm_exists(vm_name)
    disk = config.vm_disk_path(vm_name)

    if vm_mod.is_running(vm_name):
        with QMPClient(config.vm_qmp_path(vm_name)) as qmp:
            result = qmp.loadvm(snap_name)
            if result:
                raise RuntimeError(f"Revert failed: {result}")
    else:
        qemu.snapshot_revert(disk, snap_name)


def delete(vm_name: str, snap_name: str) -> None:
    """Delete a snapshot. Uses QMP if running, qemu-img otherwise."""
    _ensure_vm_exists(vm_name)
    disk = config.vm_disk_path(vm_name)

    if vm_mod.is_running(vm_name):
        with QMPClient(config.vm_qmp_path(vm_name)) as qmp:
            result = qmp.delvm(snap_name)
            if result:
                raise RuntimeError(f"Delete failed: {result}")
    else:
        qemu.snapshot_delete(disk, snap_name)


def _ensure_vm_exists(vm_name: str) -> None:
    if not config.vm_dir(vm_name).exists():
        raise FileNotFoundError(f"VM '{vm_name}' not found")
