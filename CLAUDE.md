# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Xray is a QEMU virtual machine manager for Apple Silicon Macs. It enables instant VM creation using qcow2 copy-on-write overlays, with built-in firewall controls (SOCKS5 proxy + per-connection approval via macOS dialogs), live snapshots, and lifecycle hooks.

## Development Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Requires: macOS Apple Silicon, Python 3.11+, QEMU (`brew install qemu`).

## Running

After install, the `xray` CLI is available. Key commands: `xray base import`, `xray create`, `xray start`, `xray stop`, `xray list`.

## Architecture

**Source layout:** All code is in `src/xray/`. Entry point is `cli.py` (Click command groups).

**Key modules and data flow:**

- **cli.py** — Click CLI commands, uses Rich for terminal output
- **vm.py** — VM lifecycle (create, start, stop, remove). Start flow: launches SOCKS5 proxy thread → starts QEMU process → runs hooks via SSH → waits for QEMU exit → cleanup
- **qemu.py** — Locates QEMU binaries (Homebrew paths), builds `qemu-system-aarch64` command with HVF acceleration and UEFI boot
- **proxy.py** — Async SOCKS5 proxy server that intercepts all guest outbound connections for firewall enforcement
- **firewall.py** — Rule storage/matching. Rules persist in each VM's `vm.toml`
- **notifier.py** — macOS AppleScript dialogs for allow/deny decisions on unknown connections
- **hooks.py** — Lifecycle hooks (initial-boot, boot). Scripts collected from 3 sources in order: built-in (`src/xray/scripts/`), user global (`~/.xray/scripts/`), per-VM. Scripts run on the host with `XRAY_*` env vars and SSH into the guest
- **plugins.py** — Python plugin system. Loads `.py` files from `~/.xray/plugins/`, extracts `COMMANDS` (Click commands) and `HOOKS` (Python callables keyed by hook type). Plugin commands are lazily added to the CLI via `_XrayGroup`
- **qmp.py** — QMP (QEMU Monitor Protocol) client over Unix socket for live snapshots
- **snapshot.py** — Dual-mode: QMP for running VMs (includes memory state), `qemu-img` for stopped VMs
- **ssh.py** — SSH/SCP utilities with connection-test-based wait (not just port check)
- **config.py** — Paths under `~/.xray/` (overridable via `XRAY_HOME`), VM config in TOML
- **base.py** — Base image import/removal; overlays use relative paths for portability

**Firewall data flow:** Guest iptables → redsocks → SOCKS5 proxy on host (via QEMU guestfwd at `10.0.2.100:1080`) → rule check → macOS dialog if unknown → rule persisted to `vm.toml`.

**Threading model:** SOCKS5 proxy runs in a daemon thread with its own asyncio event loop. Firewall checks use a thread pool executor to avoid blocking the proxy on macOS notification dialogs. A lock serializes simultaneous dialog prompts.

## Storage Layout

```
~/.xray/
├── bases/                          # Immutable base qcow2 images
├── vms/{name}/
│   ├── vm.toml                     # Config: base, memory, cpus, ports, firewall rules
│   ├── disk.qcow2                  # CoW overlay (relative backing path to base)
│   ├── efivars.fd                  # UEFI variable store
│   ├── qmp.sock, pid, proxy_port   # Runtime files
│   └── scripts/{initial-boot,boot}/ # Per-VM hook scripts
├── scripts/{initial-boot,boot}/    # User global hook scripts
├── plugins/                        # Python plugins (.py files with COMMANDS/HOOKS)
└── default-firewall-rules.conf     # Default allowed domains
```

## Dependencies

Python: `click`, `tomli-w`, `rich`. Standard library: `asyncio`, `tomllib` (3.11+), `subprocess`, `threading`, `socket`, `json`.
