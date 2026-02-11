"""Plugin system for xray.

Plugins are .py files in ~/.xray/plugins/. They use module-level COMMANDS
and HOOKS variables for registration — no xray imports required:

    import click

    @click.command()
    @click.argument("vm")
    def deploy(vm):
        click.echo(f"Deploying to {vm}...")

    def sync_dotfiles(vm_name, ssh_port, ssh_user, ssh_host):
        import subprocess
        subprocess.run(["scp", "-P", str(ssh_port), ...])

    COMMANDS = [deploy]
    HOOKS = {
        "boot": [sync_dotfiles],
    }
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import click

from . import config

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
        if hook_type not in ("initial-boot", "boot"):
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
    ssh_port: int,
    ssh_user: str = "ubuntu",
    ssh_host: str = "127.0.0.1",
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
        print(f"[hooks] Running {hook_type}/{fn_name} ({source})...", flush=True)

        try:
            fn(
                vm_name=vm_name,
                ssh_port=ssh_port,
                ssh_user=ssh_user,
                ssh_host=ssh_host,
            )
            results.append((source, fn_name, True, ""))
            print(f"[hooks] {fn_name} completed", flush=True)
        except Exception as e:
            results.append((source, fn_name, False, str(e)))
            print(f"[hooks] {fn_name} FAILED ({e})", flush=True)

    return results
