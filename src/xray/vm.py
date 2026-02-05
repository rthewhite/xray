"""VM creation, lifecycle, and management."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from . import config, qemu, base as base_mod
from .qmp import QMPClient, QMPError


def create(
    name: str,
    base_name: str,
    memory: int = 2048,
    cpus: int = 2,
    ports: list[str] | None = None,
) -> None:
    """Create a new VM with a qcow2 overlay on top of the given base image."""
    vm = config.vm_dir(name)
    if vm.exists():
        raise FileExistsError(f"VM '{name}' already exists")

    base_path = base_mod.get_base_path(base_name)
    disk_path = config.vm_disk_path(name)

    # Create VM directory and config
    vm.mkdir(parents=True)
    vm_cfg = {
        "base": base_name,
        "memory": memory,
        "cpus": cpus,
        "ports": ports or [],
    }
    config.write_vm_config(name, vm_cfg)

    # Create overlay — use relative path from the overlay to the base
    rel_base = os.path.relpath(base_path, disk_path.parent)
    qemu.create_overlay(Path(rel_base), disk_path)

    # Create writable UEFI variable store
    qemu.ensure_efivars(config.vm_efivars_path(name))


def remove(name: str) -> None:
    """Delete a VM and all its files."""
    vm = config.vm_dir(name)
    if not vm.exists():
        raise FileNotFoundError(f"VM '{name}' not found")
    if is_running(name):
        raise RuntimeError(f"VM '{name}' is running. Stop it first.")
    shutil.rmtree(vm)


def is_running(name: str) -> bool:
    """Check if a VM is currently running via its PID file."""
    pid_path = config.vm_pid_path(name)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # signal 0 = check if alive
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale PID file, clean up
        pid_path.unlink(missing_ok=True)
        config.vm_qmp_path(name).unlink(missing_ok=True)
        return False


def start(
    name: str,
    detach: bool = False,
    display: str = "none",
) -> subprocess.Popen | None:
    """Start a VM. Returns the Popen object if detached, None if foreground."""
    if not config.vm_dir(name).exists():
        raise FileNotFoundError(f"VM '{name}' not found")
    if is_running(name):
        raise RuntimeError(f"VM '{name}' is already running")

    vm_cfg = config.read_vm_config(name)
    disk_path = config.vm_disk_path(name)
    qmp_path = config.vm_qmp_path(name)
    pid_path = config.vm_pid_path(name)

    # Clean up stale socket
    qmp_path.unlink(missing_ok=True)

    efivars_path = config.vm_efivars_path(name)
    # Ensure efivars exists (for VMs created before this feature)
    qemu.ensure_efivars(efivars_path)

    cmd = qemu.build_start_command(
        disk_path=disk_path,
        efivars_path=efivars_path,
        qmp_sock_path=qmp_path,
        memory=vm_cfg.get("memory", 2048),
        cpus=vm_cfg.get("cpus", 2),
        display=display,
        ports=vm_cfg.get("ports", []),
    )

    if detach:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_path.write_text(str(proc.pid))
        return proc
    else:
        # Foreground — run and block
        pid_path.write_text(str(os.getpid()))
        try:
            # Replace current process with QEMU for foreground mode
            # Actually, use subprocess.run so we can clean up after
            result = subprocess.run(cmd)
            return None
        finally:
            pid_path.unlink(missing_ok=True)
            qmp_path.unlink(missing_ok=True)


def stop(name: str, force: bool = False) -> None:
    """Stop a running VM."""
    if not is_running(name):
        raise RuntimeError(f"VM '{name}' is not running")

    pid_path = config.vm_pid_path(name)
    qmp_path = config.vm_qmp_path(name)
    pid = int(pid_path.read_text().strip())

    if not force:
        # Try graceful ACPI shutdown via QMP
        try:
            with QMPClient(qmp_path) as qmp:
                qmp.system_powerdown()
            # Wait briefly for shutdown
            import time
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                    time.sleep(1)
                except ProcessLookupError:
                    break
            else:
                # Still running after 30s, force kill
                force = True
        except QMPError:
            force = True

    if force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    pid_path.unlink(missing_ok=True)
    qmp_path.unlink(missing_ok=True)


def add_port(name: str, mapping: str) -> None:
    """Add a port forwarding rule (host:guest) to a VM."""
    _validate_port_mapping(mapping)
    vm_cfg = config.read_vm_config(name)
    ports = vm_cfg.get("ports", [])
    if mapping in ports:
        raise ValueError(f"Port mapping '{mapping}' already exists")
    ports.append(mapping)
    vm_cfg["ports"] = ports
    config.write_vm_config(name, vm_cfg)


def remove_port(name: str, mapping: str) -> None:
    """Remove a port forwarding rule from a VM."""
    vm_cfg = config.read_vm_config(name)
    ports = vm_cfg.get("ports", [])
    if mapping not in ports:
        raise ValueError(f"Port mapping '{mapping}' not found")
    ports.remove(mapping)
    vm_cfg["ports"] = ports
    config.write_vm_config(name, vm_cfg)


def _validate_port_mapping(mapping: str) -> None:
    """Validate a port mapping string like '8080:80'."""
    parts = mapping.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid port mapping '{mapping}'. Use format: host_port:guest_port")
    for part in parts:
        try:
            port = int(part)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            raise ValueError(f"Invalid port number in '{mapping}'. Ports must be 1-65535")


def info(name: str) -> dict:
    """Get detailed info about a VM."""
    if not config.vm_dir(name).exists():
        raise FileNotFoundError(f"VM '{name}' not found")

    vm_cfg = config.read_vm_config(name)
    disk_path = config.vm_disk_path(name)
    running = is_running(name)

    result = {
        "name": name,
        "running": running,
        "config": vm_cfg,
    }

    if disk_path.exists():
        try:
            result["disk"] = qemu.image_info(disk_path, backing_chain=True)
        except subprocess.CalledProcessError:
            result["disk"] = None

    return result
