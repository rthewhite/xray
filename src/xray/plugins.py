"""Plugin system for xray.

Plugins are .py files in ~/.xray/plugins/. They use module-level COMMANDS
and HOOKS variables for registration — no xray imports required:

    import click

    @click.command()
    @click.argument("vm")
    def deploy(vm):
        click.echo(f"Deploying to {vm}...")

    def sync_dotfiles(vm_name, helpers):
        # Run commands in the VM
        helpers.run("mkdir -p ~/.config")
        helpers.copy_file("~/.vimrc", "~/.vimrc")
        helpers.run_script("apt-get update && apt-get install -y vim")

        # Read/write plugin settings in vm.toml (scoped to this plugin)
        helpers.set("last_sync", "2024-01-01")
        last_sync = helpers.get("last_sync")

    COMMANDS = [deploy]
    HOOKS = {
        "boot": [sync_dotfiles],
    }
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import click

from . import config, ssh as ssh_mod


class _PrefixedWriter:
    """Wraps a file object to prefix each complete line."""

    def __init__(self, wrapped, prefix: str):
        self._wrapped = wrapped
        self._prefix = prefix
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._wrapped.write(f"{self._prefix}{line}\n")
        return len(s)

    def flush(self):
        if self._buf:
            self._wrapped.write(f"{self._prefix}{self._buf}")
            self._buf = ""
        self._wrapped.flush()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class PluginHelpers:
    """Helper object passed to plugin hooks.

    Provides:
    - Scoped settings in vm.toml under [plugins.<plugin_name>]
    - SSH commands pre-bound to the VM's connection details
    """

    def __init__(self, vm_name: str, plugin_name: str):
        self._vm_name = vm_name
        self._plugin_name = plugin_name
        # Read SSH details from VM config
        vm_cfg = config.read_vm_config(vm_name)
        self._ssh_port = vm_cfg.get("ssh_port")
        self._ssh_user = vm_cfg.get("ssh_user", "ubuntu")
        self._ssh_host = "127.0.0.1"

    # ── Settings (scoped to [plugins.<plugin_name>] in vm.toml) ──

    def get(self, key: str, default=None):
        """Read a setting for this plugin."""
        vm_cfg = config.read_vm_config(self._vm_name)
        return vm_cfg.get("plugins", {}).get(self._plugin_name, {}).get(key, default)

    def get_all(self) -> dict:
        """Read all settings for this plugin."""
        vm_cfg = config.read_vm_config(self._vm_name)
        return dict(vm_cfg.get("plugins", {}).get(self._plugin_name, {}))

    def set(self, key: str, value) -> None:
        """Write a setting for this plugin."""
        vm_cfg = config.read_vm_config(self._vm_name)
        plugins_section = vm_cfg.setdefault("plugins", {})
        plugin_section = plugins_section.setdefault(self._plugin_name, {})
        plugin_section[key] = value
        config.write_vm_config(self._vm_name, vm_cfg)

    def delete(self, key: str) -> None:
        """Delete a setting for this plugin. No-op if key doesn't exist."""
        vm_cfg = config.read_vm_config(self._vm_name)
        plugin_section = vm_cfg.get("plugins", {}).get(self._plugin_name, {})
        if key in plugin_section:
            del plugin_section[key]
            # Clean up empty sections
            if not plugin_section:
                del vm_cfg["plugins"][self._plugin_name]
                if not vm_cfg["plugins"]:
                    del vm_cfg["plugins"]
            config.write_vm_config(self._vm_name, vm_cfg)

    # ── SSH helpers (pre-bound to this VM's connection) ──

    def run(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run a command in the VM via SSH.

        Returns (returncode, stdout, stderr).
        Raises RuntimeError if the command fails (non-zero exit).
        """
        rc, stdout, stderr = ssh_mod.run_command(
            self._ssh_host, self._ssh_port, command,
            user=self._ssh_user, timeout=timeout,
        )
        if rc != 0:
            raise RuntimeError(f"Command failed (exit {rc}): {stderr.strip() or stdout.strip()}")
        return rc, stdout, stderr

    def run_script(self, script_content: str, timeout: int = 300) -> tuple[int, str, str]:
        """Run a multi-line bash script in the VM.

        Returns (returncode, stdout, stderr).
        Raises RuntimeError if the script fails.
        """
        rc, stdout, stderr = ssh_mod.run_script(
            self._ssh_host, self._ssh_port, script_content,
            user=self._ssh_user, timeout=timeout,
        )
        if rc != 0:
            raise RuntimeError(f"Script failed (exit {rc}): {stderr.strip() or stdout.strip()}")
        return rc, stdout, stderr

    def copy_file(self, local_path: str, remote_path: str, timeout: int = 30) -> None:
        """Copy a local file into the VM via SCP.

        Raises RuntimeError if the copy fails.
        """
        rc, stdout, stderr = ssh_mod.copy_file(
            self._ssh_host, self._ssh_port, local_path, remote_path,
            user=self._ssh_user, timeout=timeout,
        )
        if rc != 0:
            raise RuntimeError(f"Copy failed: {stderr.strip() or stdout.strip()}")


# Module-level registry populated by load_all_plugins()
_plugin_commands: list[tuple[str, click.BaseCommand]] = []
_plugin_hooks: dict[str, list[tuple[str, callable]]] = {}


def discover_plugins() -> list[Path]:
    """Find all plugin files in the plugins directory."""
    plugins_dir = config.plugins_dir()
    if not plugins_dir.exists():
        return []
    return sorted(
        p for p in plugins_dir.glob("*.py")
        if p.is_file() and not p.name.startswith("_")
    )


def _load_plugin(path: Path) -> tuple[list[click.BaseCommand], dict[str, list[callable]]]:
    """Load a single plugin file.

    Returns:
        (commands, hooks) extracted from the plugin module.
    """
    stem = path.stem
    module_name = f"xray_plugin_{stem}"

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    commands = getattr(module, "COMMANDS", [])
    hooks = getattr(module, "HOOKS", {})

    # Validate commands
    valid_commands = []
    for cmd in commands:
        if isinstance(cmd, click.BaseCommand):
            valid_commands.append(cmd)
        else:
            print(f"[plugins] Warning: {stem}: ignoring non-command in COMMANDS: {cmd!r}")

    # Validate hooks
    valid_hooks: dict[str, list[callable]] = {}
    for hook_type, hook_list in hooks.items():
        if hook_type not in ("create", "initial-boot", "boot"):
            print(f"[plugins] Warning: {stem}: unknown hook type '{hook_type}', skipping")
            continue
        valid_fns = []
        for fn in hook_list:
            if callable(fn):
                valid_fns.append(fn)
            else:
                print(f"[plugins] Warning: {stem}: ignoring non-callable in HOOKS['{hook_type}']: {fn!r}")
        if valid_fns:
            valid_hooks[hook_type] = valid_fns

    return valid_commands, valid_hooks


def load_all_plugins() -> None:
    """Discover and load all plugins, populating the module-level registries."""
    global _plugin_commands, _plugin_hooks

    _plugin_commands = []
    _plugin_hooks = {}

    for path in discover_plugins():
        stem = path.stem
        try:
            commands, hooks = _load_plugin(path)
            for cmd in commands:
                _plugin_commands.append((stem, cmd))
            for hook_type, fns in hooks.items():
                _plugin_hooks.setdefault(hook_type, [])
                for fn in fns:
                    _plugin_hooks[hook_type].append((stem, fn))
        except Exception as e:
            print(f"[plugins] Error loading {stem}: {e}")


def get_plugin_commands() -> list[click.BaseCommand]:
    """Return all Click commands from loaded plugins."""
    return [cmd for _, cmd in _plugin_commands]


def get_plugin_hooks(hook_type: str) -> list[tuple[str, callable]]:
    """Return (plugin_name, callable) pairs for a hook type."""
    return _plugin_hooks.get(hook_type, [])


def run_plugin_hooks(
    hook_type: str,
    vm_name: str,
) -> list[tuple[str, str, bool, str]]:
    """Execute plugin hooks with error isolation.

    Returns:
        List of (source, name, success, output) tuples matching run_hook_scripts() format.
    """
    hooks = get_plugin_hooks(hook_type)
    if not hooks:
        return []

    results: list[tuple[str, str, bool, str]] = []

    for plugin_name, fn in hooks:
        source = f"plugin:{plugin_name}"
        fn_name = fn.__name__
        helpers = PluginHelpers(vm_name, plugin_name)
        print(f"[hooks] Running {hook_type}/{fn_name} ({source})...", flush=True)

        try:
            old_stdout = sys.stdout
            sys.stdout = _PrefixedWriter(old_stdout, "[hooks]   ")
            try:
                fn(
                    vm_name=vm_name,
                    helpers=helpers,
                )
            finally:
                sys.stdout.flush()
                sys.stdout = old_stdout
            results.append((source, fn_name, True, ""))
            print(f"[hooks] {fn_name} completed", flush=True)
        except Exception as e:
            results.append((source, fn_name, False, str(e)))
            print(f"[hooks] {fn_name} FAILED ({e})", flush=True)

    return results
