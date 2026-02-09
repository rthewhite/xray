"""xray CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import config, base as base_mod, vm as vm_mod, snapshot as snap_mod, qemu, hooks as hooks_mod

console = Console()


@click.group()
def main():
    """xray — Fast QEMU VM manager with qcow2 overlays and snapshots."""


# ── Base image commands ──────────────────────────────────────────────


@main.group("base")
def base_group():
    """Manage base images."""


@base_group.command("list")
def base_list():
    """List available base images."""
    bases = config.list_bases()
    if not bases:
        console.print("[dim]No base images found. Import one with:[/] xray base import <path>")
        return

    table = Table(title="Base Images")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("Link")

    for name in bases:
        info = base_mod.base_info(name)
        size_mb = info["size"] / (1024 * 1024)
        size_str = f"{size_mb:.0f} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"
        table.add_row(name, size_str, "→ " + str(Path(info["path"]).resolve()) if info["is_link"] else "")

    console.print(table)


@base_group.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", "-n", default=None, help="Name for the base image (default: filename stem)")
@click.option("--link/--copy", default=False, help="Symlink instead of copying (default: copy)")
def base_import(path: Path, name: str | None, link: bool):
    """Import a qcow2 base image."""
    try:
        result_name = base_mod.import_base(path, name=name, link=link)
        action = "Linked" if link else "Imported"
        console.print(f"[green]{action} base image:[/] {result_name}")
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@base_group.command("remove")
@click.argument("name")
def base_remove(name: str):
    """Remove a base image."""
    try:
        base_mod.remove_base(name)
        console.print(f"[green]Removed base image:[/] {name}")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


# ── VM commands ──────────────────────────────────────────────────────


@main.command("create")
@click.argument("name")
@click.option("--base", "-b", "base_name", default=None, help="Base image to use")
@click.option("--memory", "-m", default=4096, help="Memory in MB (default: 4096)")
@click.option("--cpus", "-c", default=4, help="Number of CPUs (default: 4)")
@click.option("--ssh-user", default="ubuntu", help="SSH username in guest (default: ubuntu)")
@click.option("--port", "-p", "ports", multiple=True, help="Port forward as host:guest (e.g. -p 8080:80)")
def vm_create(name: str, base_name: str | None, memory: int, cpus: int, ssh_user: str, ports: tuple[str, ...]):
    """Create a new VM from a base image.

    SSH port is automatically assigned (starting from 2222).
    """
    # Interactive base picker if none specified
    if base_name is None:
        bases = config.list_bases()
        if not bases:
            console.print("[red]No base images available.[/] Import one first: xray base import <path>")
            sys.exit(1)
        # Always show picker
        console.print("[bold]Available base images:[/]")
        for i, b in enumerate(bases, 1):
            console.print(f"  {i}. {b}")
        choice = click.prompt("Select base image", type=click.IntRange(1, len(bases)))
        base_name = bases[choice - 1]

    try:
        ssh_port = vm_mod.create(name, base_name, memory=memory, cpus=cpus, ports=list(ports), ssh_user=ssh_user)
        console.print(f"[green]Created VM:[/] {name} (base: {base_name})")
        console.print(f"[dim]SSH port:[/] {ssh_port} (ssh -p {ssh_port} {ssh_user}@localhost)")
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@main.command("list")
def vm_list():
    """List all VMs."""
    vms = config.list_vms()
    if not vms:
        console.print("[dim]No VMs found. Create one with:[/] xray create <name>")
        return

    table = Table(title="Virtual Machines")
    table.add_column("Name")
    table.add_column("Base")
    table.add_column("SSH Port", justify="right")
    table.add_column("Memory", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column("Disk", justify="right")
    table.add_column("Status")

    for name in vms:
        try:
            vm_cfg = config.read_vm_config(name)
            running = vm_mod.is_running(name)
            status = "[green]running[/]" if running else "[dim]stopped[/]"

            # Get disk size
            disk_size_str = "?"
            try:
                disk_path = config.vm_disk_path(name)
                if disk_path.exists():
                    disk_info = qemu.image_info(disk_path)
                    actual_size = disk_info.get("actual-size", 0)
                    disk_size_str = _format_bytes(actual_size)
            except Exception:
                pass

            ssh_port = vm_cfg.get("ssh_port", "?")

            table.add_row(
                name,
                vm_cfg.get("base", "?"),
                str(ssh_port),
                f"{vm_cfg.get('memory', '?')} MB",
                str(vm_cfg.get("cpus", "?")),
                disk_size_str,
                status,
            )
        except Exception:
            table.add_row(name, "?", "?", "?", "?", "?", "[red]error[/]")

    console.print(table)


@main.command("start")
@click.argument("name")
@click.option("--display", type=click.Choice(["cocoa", "none", "curses"]), default="cocoa", help="Display type (default: cocoa)")
@click.option("--no-hooks", is_flag=True, help="Skip running lifecycle hooks")
def vm_start(name: str, display: str, no_hooks: bool):
    """Start a VM.

    The VM runs in the foreground. Press Ctrl+C or close the window to stop.
    """
    try:
        console.print(f"Starting VM [bold]{name}[/] (Ctrl+C to stop)...")
        vm_mod.start(name, display=display, run_hooks=not no_hooks)
        console.print(f"[dim]VM {name} stopped.[/]")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@main.command("stop")
@click.argument("name")
@click.option("--force", "-f", is_flag=True, help="Force kill instead of graceful shutdown")
def vm_stop(name: str, force: bool):
    """Stop a running VM."""
    try:
        vm_mod.stop(name, force=force)
        console.print(f"[green]Stopped VM:[/] {name}")
    except RuntimeError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@main.command("remove")
@click.argument("name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def vm_remove(name: str, force: bool):
    """Delete a VM and all its files."""
    if not force:
        click.confirm(f"Delete VM '{name}' and all its data?", abort=True)
    try:
        vm_mod.remove(name)
        console.print(f"[green]Removed VM:[/] {name}")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@main.command("info")
@click.argument("name")
def vm_info(name: str):
    """Show detailed info about a VM."""
    try:
        data = vm_mod.info(name)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    cfg = data["config"]
    console.print(f"[bold]{data['name']}[/]")
    console.print(f"  Status:  {'[green]running[/]' if data['running'] else '[dim]stopped[/]'}")
    console.print(f"  Base:    {cfg.get('base', '?')}")
    console.print(f"  Memory:  {cfg.get('memory', '?')} MB")
    console.print(f"  CPUs:    {cfg.get('cpus', '?')}")
    ports = cfg.get("ports", [])
    if ports:
        console.print(f"  Ports:   {', '.join(ports)}")

    disk = data.get("disk")
    if disk:
        # qemu-img info --backing-chain returns a list when using --output=json
        images = disk if isinstance(disk, list) else [disk]
        console.print(f"\n  [bold]Disk chain:[/]")
        for i, img in enumerate(images):
            vsize = img.get("virtual-size", 0)
            asize = img.get("actual-size", 0)
            vsize_str = _format_bytes(vsize)
            asize_str = _format_bytes(asize)
            fname = img.get("filename", "?")
            prefix = "  └─" if i == len(images) - 1 else "  ├─"
            console.print(f"  {prefix} {fname}")
            console.print(f"  {'  ' if i == len(images) - 1 else '│ '}   virtual: {vsize_str}, actual: {asize_str}")

    # Show snapshots
    try:
        snap_output = snap_mod.list_snapshots(name)
        if snap_output and snap_output.strip():
            console.print(f"\n  [bold]Snapshots:[/]")
            for line in snap_output.strip().splitlines():
                console.print(f"    {line}")
    except Exception:
        pass


# ── Port forwarding commands ─────────────────────────────────────────


@main.group("port")
def port_group():
    """Manage VM port forwarding (requires restart to take effect)."""


@port_group.command("add")
@click.argument("vm")
@click.argument("mapping", metavar="HOST:GUEST")
def port_add(vm: str, mapping: str):
    """Add a port forward rule (e.g. xray port add my-vm 8080:80)."""
    try:
        vm_mod.add_port(vm, mapping)
        console.print(f"[green]Added port forward:[/] {mapping}")
        if vm_mod.is_running(vm):
            console.print("[yellow]Restart the VM for changes to take effect.[/]")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@port_group.command("list")
@click.argument("vm")
def port_list(vm: str):
    """List port forwarding rules for a VM."""
    try:
        vm_cfg = config.read_vm_config(vm)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    ports = vm_cfg.get("ports", [])
    if not ports:
        console.print(f"[dim]No port forwards for VM '{vm}'[/]")
        return

    table = Table(title=f"Port Forwards — {vm}")
    table.add_column("Host Port", justify="right")
    table.add_column("Guest Port", justify="right")
    for p in ports:
        host, guest = p.split(":")
        table.add_row(host, guest)
    console.print(table)


@port_group.command("remove")
@click.argument("vm")
@click.argument("mapping", metavar="HOST:GUEST")
def port_remove(vm: str, mapping: str):
    """Remove a port forward rule (e.g. xray port remove my-vm 8080:80)."""
    try:
        vm_mod.remove_port(vm, mapping)
        console.print(f"[green]Removed port forward:[/] {mapping}")
        if vm_mod.is_running(vm):
            console.print("[yellow]Restart the VM for changes to take effect.[/]")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


# ── Firewall commands ────────────────────────────────────────────────

@main.group("firewall")
def firewall_group():
    """Manage VM firewall rules."""


@firewall_group.command("list")
@click.argument("vm")
def firewall_list(vm: str):
    """List firewall rules for a VM."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    rules = config.read_firewall_rules(vm)

    if not rules:
        console.print(f"No firewall rules for VM '{vm}'")
        return

    table = Table(title=f"Firewall Rules for '{vm}'")
    table.add_column("Destination", style="cyan")
    table.add_column("Action", style="green")

    for dest, action in sorted(rules.items()):
        action_color = "green" if action == "allow" else "red"
        table.add_row(dest, f"[{action_color}]{action}[/]")

    console.print(table)


@firewall_group.command("add")
@click.argument("vm")
@click.argument("destination")  # Format: IP:PORT
@click.argument("action", type=click.Choice(["allow", "deny"]))
def firewall_add(vm: str, destination: str, action: str):
    """Add a firewall rule (e.g. xray firewall add my-vm 1.1.1.1:443 allow)."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    # Parse destination
    try:
        ip, port_str = destination.rsplit(":", 1)
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        console.print(f"[red]Invalid destination '{destination}'. Use format: IP:PORT[/]")
        sys.exit(1)

    config.add_firewall_rule(vm, ip, port, action)
    action_color = "green" if action == "allow" else "red"
    console.print(f"[{action_color}]Rule added: {destination} -> {action}[/]")


@firewall_group.command("remove")
@click.argument("vm")
@click.argument("destination")  # Format: IP:PORT
def firewall_remove(vm: str, destination: str):
    """Remove a firewall rule (e.g. xray firewall remove my-vm 1.1.1.1:443)."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    vm_cfg = config.read_vm_config(vm)
    firewall = vm_cfg.get("firewall", {})

    if destination not in firewall:
        console.print(f"[yellow]Rule '{destination}' not found[/]")
        sys.exit(1)

    del firewall[destination]
    vm_cfg["firewall"] = firewall
    config.write_vm_config(vm, vm_cfg)
    console.print(f"[green]Rule removed: {destination}[/]")


@firewall_group.command("clear")
@click.argument("vm")
@click.confirmation_option(prompt="Clear all firewall rules?")
def firewall_clear(vm: str):
    """Clear all firewall rules for a VM."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    vm_cfg = config.read_vm_config(vm)
    vm_cfg["firewall"] = {}
    config.write_vm_config(vm, vm_cfg)
    console.print(f"[green]All firewall rules cleared for '{vm}'[/]")


@firewall_group.command("status")
@click.argument("vm")
def firewall_status(vm: str):
    """Show firewall status for a VM."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    vm_cfg = config.read_vm_config(vm)
    rules = vm_cfg.get("firewall", {})

    console.print(f"[bold]Firewall Status for '{vm}'[/]")
    console.print()
    console.print(f"Rules: {len(rules)}")

    if rules:
        allow_count = sum(1 for v in rules.values() if v == "allow")
        deny_count = sum(1 for v in rules.values() if v == "deny")
        console.print(f"  [green]Allow: {allow_count}[/]")
        console.print(f"  [red]Deny: {deny_count}[/]")


# ── Hooks commands ──────────────────────────────────────────────────


@main.group("hooks")
def hooks_group():
    """Manage VM lifecycle hooks.

    Scripts are merged from three sources (in order):
    1. xray built-in scripts
    2. User global scripts (~/.xray/scripts/{hook_type}/)
    3. Per-VM scripts (~/.xray/vms/{vm}/scripts/{hook_type}/)

    Hook types: initial-boot, boot
    """


@hooks_group.command("list")
@click.argument("vm")
def hooks_list(vm: str):
    """List all hooks that will run for a VM."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    all_hooks = hooks_mod.list_all_hooks(vm)
    first_boot_done = hooks_mod.is_first_boot_completed(vm)

    for hook_type in hooks_mod.HOOK_TYPES:
        scripts = all_hooks.get(hook_type, [])

        # Add status indicator for initial-boot
        status = ""
        if hook_type == "initial-boot" and first_boot_done:
            status = " [dim](already run)[/]"

        console.print(f"\n[bold cyan]{hook_type}[/]{status}")

        if not scripts:
            console.print("  [dim]No scripts[/]")
        else:
            for source, script_name in scripts:
                source_color = {"xray": "blue", "user": "green", "vm": "yellow"}.get(source, "white")
                console.print(f"  [{source_color}][{source}][/] {script_name}")

    # Show paths
    console.print(f"\n[dim]Script locations:[/]")
    console.print(f"  [dim]User global:[/] {hooks_mod.user_scripts_dir()}")
    console.print(f"  [dim]Per-VM:[/] {hooks_mod.vm_scripts_dir(vm)}")


@hooks_group.command("run")
@click.argument("vm")
@click.argument("hook_type", type=click.Choice(["initial-boot", "boot"]))
@click.option("--user", "-u", default=None, help="SSH username (default: from VM config)")
def hooks_run(vm: str, hook_type: str, user: str | None):
    """Manually run hooks for a VM (VM must be running)."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    if not vm_mod.is_running(vm):
        console.print(f"[red]VM '{vm}' is not running[/]")
        sys.exit(1)

    scripts = hooks_mod.get_hook_scripts(vm, hook_type)
    if not scripts:
        console.print(f"[yellow]No {hook_type} scripts configured[/]")
        return

    # Get SSH user from config if not provided
    if user is None:
        vm_cfg = config.read_vm_config(vm)
        user = vm_cfg.get("ssh_user", "ubuntu")

    console.print(f"Running {len(scripts)} {hook_type} script(s)...")
    results = hooks_mod.run_hook_scripts(vm, hook_type, ssh_user=user)

    # Show results
    success_count = sum(1 for _, _, success, _ in results if success)
    fail_count = len(results) - success_count

    if fail_count > 0:
        console.print(f"\n[red]Completed with {fail_count} failure(s)[/]")
        for source, name, success, output in results:
            if not success:
                console.print(f"  [red]FAILED:[/] [{source}] {name}")
                console.print(f"    {output}")
        sys.exit(1)
    else:
        console.print(f"[green]All {success_count} script(s) completed successfully[/]")


@hooks_group.command("reset-initial-boot")
@click.argument("vm")
def hooks_reset_initial_boot(vm: str):
    """Reset initial-boot flag so initial-boot scripts run again."""
    if not config.vm_dir(vm).exists():
        console.print(f"[red]VM '{vm}' not found[/]")
        sys.exit(1)

    vm_cfg = config.read_vm_config(vm)
    vm_cfg["first_boot_completed"] = False
    config.write_vm_config(vm, vm_cfg)
    console.print(f"[green]Reset initial-boot flag for '{vm}'[/]")
    console.print("[dim]initial-boot scripts will run on next start[/]")


@hooks_group.command("init")
@click.argument("vm", required=False)
def hooks_init(vm: str | None):
    """Create scripts directories for user/VM hooks.

    Without VM argument: creates ~/.xray/scripts/{hook_type}/
    With VM argument: also creates ~/.xray/vms/{vm}/scripts/{hook_type}/
    """
    hooks_mod.ensure_scripts_dirs(vm)

    console.print("[green]Created scripts directories:[/]")
    console.print(f"  {hooks_mod.user_scripts_dir()}/")
    for hook_type in hooks_mod.HOOK_TYPES:
        console.print(f"    {hook_type}/")

    if vm:
        console.print(f"  {hooks_mod.vm_scripts_dir(vm)}/")
        for hook_type in hooks_mod.HOOK_TYPES:
            console.print(f"    {hook_type}/")


# ── Snapshot commands ────────────────────────────────────────────────


@main.group("snapshot")
def snapshot_group():
    """Manage VM snapshots."""


@snapshot_group.command("create")
@click.argument("vm")
@click.argument("snap_name")
def snap_create(vm: str, snap_name: str):
    """Create a snapshot of a VM."""
    try:
        snap_mod.create(vm, snap_name)
        live = " (live)" if vm_mod.is_running(vm) else ""
        console.print(f"[green]Created snapshot:[/] {snap_name}{live}")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@snapshot_group.command("list")
@click.argument("vm")
def snap_list(vm: str):
    """List snapshots of a VM."""
    try:
        output = snap_mod.list_snapshots(vm)
        if output and output.strip():
            console.print(output.strip())
        else:
            console.print(f"[dim]No snapshots for VM '{vm}'[/]")
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@snapshot_group.command("revert")
@click.argument("vm")
@click.argument("snap_name")
def snap_revert(vm: str, snap_name: str):
    """Revert a VM to a snapshot."""
    try:
        snap_mod.revert(vm, snap_name)
        console.print(f"[green]Reverted to snapshot:[/] {snap_name}")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@snapshot_group.command("delete")
@click.argument("vm")
@click.argument("snap_name")
def snap_delete(vm: str, snap_name: str):
    """Delete a snapshot."""
    try:
        snap_mod.delete(vm, snap_name)
        console.print(f"[green]Deleted snapshot:[/] {snap_name}")
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
