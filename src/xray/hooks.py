"""VM lifecycle hooks for xray.

Hook scripts are organized in folders and merged from multiple sources:
1. xray built-in hooks (in xray package)
2. User global hooks (~/.xray/scripts/{hook_type}/)
3. Per-VM hooks (~/.xray/vms/{vm_name}/scripts/{hook_type}/)

Hook types:
- initial-boot: Run once on first boot (tracked via first_boot_completed flag)
- boot: Run every time the VM starts

Scripts are executed in alphabetical order within each source,
with sources merged in order: xray -> user global -> per-vm.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from . import config, ssh


# Hook type names (folder names)
HOOK_TYPES = ("create", "initial-boot", "boot")


def _xray_scripts_dir() -> Path:
    """Get path to xray's built-in scripts directory."""
    # In development, use the path relative to this file
    # This avoids issues with importlib.resources context manager
    dev_path = Path(__file__).parent / "scripts"
    if dev_path.exists():
        return dev_path

    # For installed package, use importlib.resources
    try:
        # Get the path - note: this only works for packages installed in a way
        # that provides real filesystem paths (not zip imports)
        files = importlib.resources.files("xray")
        scripts = files / "scripts"
        # Try to get a real path (works for most installations)
        if hasattr(scripts, "_path"):
            return Path(scripts._path)
        # Fallback: use as_file but we need to be careful here
        # For now, just use the dev path as primary
        return dev_path
    except (TypeError, FileNotFoundError, AttributeError):
        return dev_path


def user_scripts_dir() -> Path:
    """Get path to user's global scripts directory."""
    return config.xray_home() / "scripts"


def vm_scripts_dir(name: str) -> Path:
    """Get path to a VM's scripts directory."""
    return config.vm_dir(name) / "scripts"


def _ensureuser_scripts_dirs() -> None:
    """Ensure user scripts directories exist."""
    base = user_scripts_dir()
    base.mkdir(exist_ok=True)
    for hook_type in HOOK_TYPES:
        (base / hook_type).mkdir(exist_ok=True)


def _ensurevm_scripts_dirs(name: str) -> None:
    """Ensure VM scripts directories exist."""
    base = vm_scripts_dir(name)
    base.mkdir(exist_ok=True)
    for hook_type in HOOK_TYPES:
        (base / hook_type).mkdir(exist_ok=True)


def _get_scripts_from_dir(directory: Path) -> list[Path]:
    """Get all .sh scripts from a directory, sorted alphabetically."""
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.glob("*.sh")
        if p.is_file()
    )


def get_hook_scripts(name: str, hook_type: str) -> list[tuple[str, Path]]:
    """Get all scripts for a hook type, merged from all sources.

    Args:
        name: VM name
        hook_type: One of initial-boot, boot, after-shutdown

    Returns:
        List of (source_name, script_path) tuples in execution order.
        source_name is one of: "xray", "user", "vm"
    """
    if hook_type not in HOOK_TYPES:
        raise ValueError(f"Invalid hook type: {hook_type}. Must be one of {HOOK_TYPES}")

    scripts: list[tuple[str, Path]] = []

    # 1. xray built-in hooks
    xray_dir = _xray_scripts_dir() / hook_type
    for script in _get_scripts_from_dir(xray_dir):
        scripts.append(("xray", script))

    # 2. User global hooks
    user_dir = user_scripts_dir() / hook_type
    for script in _get_scripts_from_dir(user_dir):
        scripts.append(("user", script))

    # 3. Per-VM hooks
    vm_dir = vm_scripts_dir(name) / hook_type
    for script in _get_scripts_from_dir(vm_dir):
        scripts.append(("vm", script))

    return scripts


def list_all_hooks(name: str) -> dict[str, list[tuple[str, str]]]:
    """List all hooks for a VM across all hook types.

    Returns:
        Dict mapping hook_type -> list of (source, script_name) tuples
    """
    from . import plugins

    result = {}
    for hook_type in HOOK_TYPES:
        entries: list[tuple[str, str]] = []
        # Shell script hooks
        scripts = get_hook_scripts(name, hook_type)
        entries.extend((source, script.name) for source, script in scripts)
        # Plugin hooks
        for plugin_name, fn in plugins.get_plugin_hooks(hook_type):
            entries.append((f"plugin:{plugin_name}", fn.__name__))
        result[hook_type] = entries
    return result


def is_first_boot_completed(name: str) -> bool:
    """Check if first boot hook has already run."""
    vm_cfg = config.read_vm_config(name)
    return vm_cfg.get("first_boot_completed", False)


def mark_first_boot_completed(name: str) -> None:
    """Mark first boot as completed."""
    vm_cfg = config.read_vm_config(name)
    vm_cfg["first_boot_completed"] = True
    config.write_vm_config(name, vm_cfg)


def run_hook_scripts(
    name: str,
    hook_type: str,
    ssh_user: str = "ubuntu",
    timeout_per_script: int = 300,
) -> list[tuple[str, str, bool, str]]:
    """Run all scripts for a hook type ON THE HOST.

    Scripts receive environment variables:
    - XRAY_VM_NAME: VM name
    - XRAY_SSH_PORT: SSH port on localhost
    - XRAY_SSH_USER: SSH username
    - XRAY_SSH_HOST: Always "127.0.0.1"

    Scripts can use these to SSH/SCP into the VM as needed.

    Args:
        name: VM name
        hook_type: One of initial-boot, boot
        ssh_user: SSH username
        timeout_per_script: Timeout per script in seconds

    Returns:
        List of (source, script_name, success, output) tuples
    """
    from . import firewall  # Import here to avoid circular import
    import subprocess
    import os

    scripts = get_hook_scripts(name, hook_type)
    if not scripts:
        return []

    # Get SSH port for environment
    ssh_port = firewall.get_ssh_port(name)
    if ssh_port is None:
        return [("error", "", False, "No SSH port configured for VM")]

    # Wait for SSH if needed (for boot hooks)
    if hook_type in ("initial-boot", "boot"):
        print(f"[hooks] Waiting for SSH on port {ssh_port}...", flush=True)
        if not ssh.wait_for_ssh("127.0.0.1", ssh_port, timeout=120):
            return [("error", "", False, "SSH not available after 120 seconds")]

    # Build environment for scripts
    script_env = os.environ.copy()
    script_env.update({
        "XRAY_VM_NAME": name,
        "XRAY_SSH_PORT": str(ssh_port),
        "XRAY_SSH_USER": ssh_user,
        "XRAY_SSH_HOST": "127.0.0.1",
    })

    results: list[tuple[str, str, bool, str]] = []

    for source, script_path in scripts:
        script_name = script_path.name
        print(f"[hooks] Running {hook_type}/{script_name} ({source})...", flush=True)

        try:
            result = subprocess.run(
                [str(script_path)],
                env=script_env,
                text=True,
                capture_output=True,
                timeout=timeout_per_script,
                cwd=str(script_path.parent),
            )

            # Print captured output with prefix
            for stream in (result.stdout, result.stderr):
                if stream:
                    for line in stream.splitlines():
                        print(f"[hooks]   {line}", flush=True)

            if result.returncode != 0:
                results.append((source, script_name, False, f"Exit code {result.returncode}"))
                print(f"[hooks] {script_name} FAILED (exit code {result.returncode})", flush=True)
            else:
                results.append((source, script_name, True, ""))
                print(f"[hooks] {script_name} completed", flush=True)

        except subprocess.TimeoutExpired:
            results.append((source, script_name, False, f"Script timed out after {timeout_per_script}s"))
            print(f"[hooks] {script_name} FAILED (timeout)", flush=True)
        except PermissionError:
            results.append((source, script_name, False, "Permission denied - make sure script is executable"))
            print(f"[hooks] {script_name} FAILED (not executable)", flush=True)
        except Exception as e:
            results.append((source, script_name, False, str(e)))
            print(f"[hooks] {script_name} FAILED ({e})", flush=True)

    return results


def run_boot_hooks(name: str, ssh_user: str = "ubuntu") -> None:
    """Run boot hooks for a VM (called after VM starts).

    Runs initial-boot (if not done) then boot. Includes plugin hooks.
    """
    from . import plugins

    # Check if initial boot hooks need to run
    if not is_first_boot_completed(name):
        scripts = get_hook_scripts(name, "initial-boot")
        plugin_hooks = plugins.get_plugin_hooks("initial-boot")
        if scripts or plugin_hooks:
            print(f"[hooks] Running initial-boot hooks for '{name}'...", flush=True)
            results = run_hook_scripts(name, "initial-boot", ssh_user=ssh_user)
            if plugin_hooks:
                results.extend(plugins.run_plugin_hooks("initial-boot", name))
            # Only mark as completed if all hooks succeeded
            all_success = all(success for _, _, success, _ in results)
            if all_success:
                mark_first_boot_completed(name)
                print(f"[hooks] initial-boot completed for '{name}'", flush=True)
            else:
                failed = [(s, n) for s, n, success, _ in results if not success]
                print(f"[hooks] initial-boot FAILED for '{name}': {failed}", flush=True)
        else:
            # No initial-boot hooks, mark as completed
            mark_first_boot_completed(name)

    # Run boot hooks
    scripts = get_hook_scripts(name, "boot")
    plugin_hooks = plugins.get_plugin_hooks("boot")
    if scripts or plugin_hooks:
        print(f"[hooks] Running boot hooks for '{name}'...", flush=True)
        results = run_hook_scripts(name, "boot", ssh_user=ssh_user)
        if plugin_hooks:
            results.extend(plugins.run_plugin_hooks("boot", name))
        failed = [(s, n) for s, n, success, _ in results if not success]
        if failed:
            print(f"[hooks] boot had failures for '{name}': {failed}", flush=True)
        else:
            print(f"[hooks] boot completed for '{name}'", flush=True)




def ensure_scripts_dirs(name: str | None = None) -> None:
    """Ensure scripts directories exist.

    Args:
        name: If provided, also create VM-specific scripts dirs
    """
    _ensureuser_scripts_dirs()
    if name:
        _ensurevm_scripts_dirs(name)
