"""VM creation, lifecycle, and management."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import config, enrichment, firewall, hooks, proxy, qemu, base as base_mod
from .qmp import QMPClient, QMPError


def create(
    name: str,
    base_name: str,
    memory: int = 2048,
    cpus: int = 2,
    ports: list[str] | None = None,
    ssh_user: str = "ubuntu",
) -> int:
    """Create a new VM with a qcow2 overlay on top of the given base image.

    Returns:
        The assigned SSH port number.
    """
    vm = config.vm_dir(name)
    if vm.exists():
        raise FileExistsError(f"VM '{name}' already exists")

    base_path = base_mod.get_base_path(base_name)
    disk_path = config.vm_disk_path(name)

    # Auto-assign SSH port
    ssh_port = config.next_available_ssh_port()

    # Create VM directory and config
    vm.mkdir(parents=True)
    vm_cfg = {
        "base": base_name,
        "memory": memory,
        "cpus": cpus,
        "ports": ports or [],
        "ssh_port": ssh_port,
        "ssh_user": ssh_user,
    }
    config.write_vm_config(name, vm_cfg)

    # Create overlay — use relative path from the overlay to the base
    rel_base = os.path.relpath(base_path, disk_path.parent)
    qemu.create_overlay(Path(rel_base), disk_path)

    # Create writable UEFI variable store
    qemu.ensure_efivars(config.vm_efivars_path(name))

    # Create scripts directories for hooks
    hooks.ensure_scripts_dirs(name)

    return ssh_port


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
    display: str = "cocoa",
    run_hooks: bool = True,
    allow_all: bool = False,
) -> None:
    """Start a VM with firewall proxy. Runs in foreground until VM shuts down.

    Args:
        name: VM name
        display: Display type (cocoa, none, curses)
        run_hooks: Run lifecycle hooks (default True)
        allow_all: Allow all firewall requests without prompting or persisting
    """
    if not config.vm_dir(name).exists():
        raise FileNotFoundError(f"VM '{name}' not found")
    if is_running(name):
        raise RuntimeError(f"VM '{name}' is already running")

    # Start the SOCKS5 proxy in a background thread
    proxy_port_file = config.vm_dir(name) / "proxy_port"
    # Clean up stale proxy port file from previous failed starts
    proxy_port_file.unlink(missing_ok=True)

    proxy.start_thread(name, proxy_port_file, allow_all, firewall.check_rule)

    # Wait for proxy to start and write port
    timeout = 5.0
    while timeout > 0:
        if proxy_port_file.exists():
            content = proxy_port_file.read_text().strip()
            if content:  # Make sure file has content, not just created
                break
        time.sleep(0.1)
        timeout -= 0.1

    if not proxy_port_file.exists() or not proxy_port_file.read_text().strip():
        raise RuntimeError("Proxy failed to start")

    proxy_port = int(proxy_port_file.read_text().strip())

    # Verify proxy is actually listening before starting QEMU
    import socket
    for _ in range(10):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(("127.0.0.1", proxy_port))
            sock.close()
            break
        except (ConnectionRefusedError, socket.timeout):
            time.sleep(0.1)
    else:
        raise RuntimeError(f"Proxy not listening on port {proxy_port}")

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
        ssh_port=vm_cfg.get("ssh_port"),
        proxy_port=proxy_port,
    )

    # Start QEMU in background so we can run hooks while it boots
    proc = subprocess.Popen(cmd)
    pid_path.write_text(str(proc.pid))

    # Run boot hooks (blocking)
    if run_hooks:
        try:
            ssh_user = vm_cfg.get("ssh_user", "ubuntu")
            hooks.run_boot_hooks(name, ssh_user=ssh_user)
        except Exception as e:
            print(f"[hooks] Error running boot hooks: {e}")

    # Wait for QEMU to exit, monitoring proxy health
    try:
        while True:
            try:
                proc.wait(timeout=5)
                break  # QEMU exited
            except subprocess.TimeoutExpired:
                # Check if proxy thread is still alive
                if not proxy.is_thread_alive(name):
                    print("[proxy] WARNING: Proxy thread died — VM has no internet", flush=True)
    finally:
        # Stop the proxy server cleanly
        proxy.stop(name)
        # Clean up enrichment caches
        enrichment.clear_vm_state(name)
        # Clean up files
        pid_path.unlink(missing_ok=True)
        qmp_path.unlink(missing_ok=True)
        proxy_port_file.unlink(missing_ok=True)


def stop(name: str, force: bool = False) -> None:
    """Stop a running VM.

    Args:
        name: VM name
        force: Force kill instead of graceful shutdown
    """
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

    # Stop the proxy server gracefully
    proxy.stop(name)

    # Clean up proxy port file
    proxy_port_file = config.vm_dir(name) / "proxy_port"
    proxy_port_file.unlink(missing_ok=True)

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
