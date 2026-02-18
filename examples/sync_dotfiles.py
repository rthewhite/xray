"""Example xray plugin: sync dotfiles on every boot.

Install by copying to ~/.xray/plugins/sync_dotfiles.py

This plugin:
- Adds an `xray dotfiles` command to check sync status
- Registers a boot hook that rsyncs your dotfiles into the VM
"""

import subprocess
from pathlib import Path

import click


# --- Configuration -----------------------------------------------------------

# Which dotfiles to sync (relative to $HOME)
DOTFILES = [
    ".bashrc",
    ".gitconfig",
    ".vimrc",
    ".tmux.conf",
]


# --- CLI command -------------------------------------------------------------

@click.command()
@click.argument("vm")
def dotfiles(vm):
    """Show which dotfiles would be synced to a VM."""
    home = Path.home()
    click.echo(f"Dotfiles to sync to '{vm}':")
    for name in DOTFILES:
        path = home / name
        status = "found" if path.exists() else "missing, will skip"
        click.echo(f"  {name} ({status})")


# --- Boot hook ---------------------------------------------------------------

def sync_dotfiles(vm_name, ssh_port, ssh_user, ssh_host, helpers):
    """Rsync dotfiles into the VM on every boot."""
    home = Path.home()

    for name in DOTFILES:
        src = home / name
        if not src.exists():
            continue

        dest = f"{ssh_user}@{ssh_host}:~/{name}"
        result = subprocess.run(
            [
                "scp",
                "-P", str(ssh_port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-q",
                str(src),
                dest,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"  synced {name}")
        else:
            print(f"  failed to sync {name}: {result.stderr.strip()}")


# --- Registration ------------------------------------------------------------

COMMANDS = [dotfiles]
HOOKS = {
    "boot": [sync_dotfiles],
}
