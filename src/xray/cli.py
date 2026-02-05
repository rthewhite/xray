"""xray CLI entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import config, base as base_mod, vm as vm_mod, snapshot as snap_mod, qemu

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
@click.option("--ssh-port", default=None, type=int, help="Host port to forward to guest port 22")
@click.option("--port", "-p", "ports", multiple=True, help="Port forward as host:guest (e.g. -p 8080:80)")
def vm_create(name: str, base_name: str | None, memory: int, cpus: int, ssh_port: int | None, ports: tuple[str, ...]):
    """Create a new VM from a base image."""
    # Interactive base picker if none specified
    if base_name is None:
        bases = config.list_bases()
        if not bases:
            console.print("[red]No base images available.[/] Import one first: xray base import <path>")
            sys.exit(1)
        if len(bases) == 1:
            base_name = bases[0]
            console.print(f"Using base image: [bold]{base_name}[/]")
        else:
            console.print("[bold]Available base images:[/]")
            for i, b in enumerate(bases, 1):
                console.print(f"  {i}. {b}")
            choice = click.prompt("Select base image", type=click.IntRange(1, len(bases)))
            base_name = bases[choice - 1]

    # Build ports list
    port_list = list(ports)
    if ssh_port:
        port_list.append(f"{ssh_port}:22")

    try:
        vm_mod.create(name, base_name, memory=memory, cpus=cpus, ports=port_list)
        console.print(f"[green]Created VM:[/] {name} (base: {base_name})")
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

            table.add_row(
                name,
                vm_cfg.get("base", "?"),
                f"{vm_cfg.get('memory', '?')} MB",
                str(vm_cfg.get("cpus", "?")),
                disk_size_str,
                status,
            )
        except Exception:
            table.add_row(name, "?", "?", "?", "?", "[red]error[/]")

    console.print(table)


@main.command("start")
@click.argument("name")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (blocking)")
@click.option("--display", type=click.Choice(["cocoa", "none", "curses"]), default="cocoa", help="Display type (default: cocoa)")
def vm_start(name: str, foreground: bool, display: str):
    """Start a VM."""
    try:
        if not foreground:
            proc = vm_mod.start(name, detach=True, display=display)
            console.print(f"[green]Started VM:[/] {name} (PID: {proc.pid})")
        else:
            console.print(f"Starting VM [bold]{name}[/] in foreground (Ctrl+C to stop)...")
            vm_mod.start(name, detach=False, display=display)
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
