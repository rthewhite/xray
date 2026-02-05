"""Low-level QEMU command builders and binary detection."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import config


def find_binary(name: str) -> str:
    """Find a QEMU binary on PATH, raising if not found."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(
            f"'{name}' not found. Install QEMU: brew install qemu"
        )
    return path


def qemu_img() -> str:
    return find_binary("qemu-img")


def qemu_system() -> str:
    return find_binary("qemu-system-aarch64")


def find_firmware() -> str:
    """Find the aarch64 UEFI firmware code file (read-only)."""
    candidates = [
        Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd"),
        Path("/usr/local/share/qemu/edk2-aarch64-code.fd"),
        Path("/usr/share/qemu/edk2-aarch64-code.fd"),
        Path("/usr/share/AAVMF/AAVMF_CODE.fd"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "UEFI firmware for aarch64 not found. Install QEMU: brew install qemu"
    )


def find_firmware_vars_template() -> str:
    """Find the UEFI variable store template file."""
    candidates = [
        Path("/opt/homebrew/share/qemu/edk2-arm-vars.fd"),
        Path("/usr/local/share/qemu/edk2-arm-vars.fd"),
        Path("/usr/share/qemu/edk2-arm-vars.fd"),
        Path("/usr/share/AAVMF/AAVMF_VARS.fd"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "UEFI variable store template not found. Install QEMU: brew install qemu"
    )


def ensure_efivars(efivars_path: Path) -> None:
    """Copy the UEFI vars template to the VM directory if not present."""
    if efivars_path.exists():
        return
    import shutil as _shutil
    template = find_firmware_vars_template()
    _shutil.copy2(template, efivars_path)


def create_overlay(backing_file: Path, overlay_path: Path) -> None:
    """Create a qcow2 overlay image backed by the given file."""
    subprocess.run(
        [
            qemu_img(), "create",
            "-f", "qcow2",
            "-b", str(backing_file),
            "-F", "qcow2",
            str(overlay_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def image_info(image_path: Path, backing_chain: bool = False) -> dict:
    """Get qemu-img info as a dict."""
    cmd = [qemu_img(), "info", "--output=json"]
    if backing_chain:
        cmd.append("--backing-chain")
    cmd.append(str(image_path))
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def snapshot_create(image_path: Path, name: str) -> None:
    subprocess.run(
        [qemu_img(), "snapshot", "-c", name, str(image_path)],
        check=True, capture_output=True, text=True,
    )


def snapshot_list(image_path: Path) -> str:
    result = subprocess.run(
        [qemu_img(), "snapshot", "-l", str(image_path)],
        check=True, capture_output=True, text=True,
    )
    return result.stdout


def snapshot_revert(image_path: Path, name: str) -> None:
    subprocess.run(
        [qemu_img(), "snapshot", "-a", name, str(image_path)],
        check=True, capture_output=True, text=True,
    )


def snapshot_delete(image_path: Path, name: str) -> None:
    subprocess.run(
        [qemu_img(), "snapshot", "-d", name, str(image_path)],
        check=True, capture_output=True, text=True,
    )


def build_start_command(
    disk_path: Path,
    efivars_path: Path,
    qmp_sock_path: Path,
    memory: int = 2048,
    cpus: int = 2,
    display: str = "cocoa",
    ports: list[str] | None = None,
) -> list[str]:
    """Build the qemu-system-aarch64 command line."""
    firmware = find_firmware()
    cmd = [
        qemu_system(),
        "-accel", "hvf",
        "-machine", "virt",
        "-cpu", "host",
        "-m", str(memory),
        "-smp", str(cpus),
        # UEFI firmware (read-only code + writable vars)
        "-drive", f"if=pflash,format=raw,readonly=on,file={firmware}",
        "-drive", f"if=pflash,format=raw,snapshot=on,file={efivars_path}",
        # Disk
        "-drive", f"if=virtio,format=qcow2,file={disk_path}",
        # USB controller + input devices
        "-device", "qemu-xhci",
        "-device", "usb-kbd",
        "-device", "usb-tablet",
        # GPU
        "-device", "virtio-gpu-pci",
        # Network
        "-device", "virtio-net-pci,netdev=net0",
    ]

    # Network with port forwards
    netdev = "user,id=net0"
    for port in (ports or []):
        host_port, guest_port = port.split(":")
        netdev += f",hostfwd=tcp::{host_port}-:{guest_port}"
    cmd += ["-netdev", netdev]

    # Claude credentials directory via virtio-9p (always enabled)
    claude_dir = config.claude_creds_dir()
    cmd += [
        "-virtfs",
        f"local,path={claude_dir},mount_tag=claude_creds,security_model=mapped-xattr"
    ]

    # QMP socket for management
    cmd += ["-qmp", f"unix:{qmp_sock_path},server,nowait"]

    # Display
    if display == "none":
        cmd += ["-nographic"]
    else:
        cmd += ["-display", display]

    return cmd
