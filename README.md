# xray

Fast QEMU VM manager with qcow2 overlays and snapshots. Create new VMs instantly from base images using copy-on-write, and take snapshots of running or stopped VMs.

## Install

Requires Python 3.11+ and QEMU:

```bash
brew install qemu
```

Then install xray:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## Quick start

```bash
# 1. Import a base image
xray base import ~/Downloads/ubuntu-24.04-arm64.qcow2 --name ubuntu

# 2. Create a VM (instant — uses a copy-on-write overlay)
xray create my-vm --base ubuntu --memory 4096 --cpus 4

# 3. Start the VM (runs in background)
xray start my-vm

# 4. Check VM status
xray list

# 5. Take a snapshot (works while running or stopped)
xray snapshot create my-vm clean-install

# 6. Break something, then revert
xray snapshot revert my-vm clean-install
```

## Base images

Base images are qcow2 files that serve as read-only templates. VMs never modify the base — all changes go to a thin overlay.

```bash
# Import by copying the file into xray's storage
xray base import /path/to/image.qcow2 --name debian

# Import by symlinking (saves disk space, but don't move the original)
xray base import /path/to/image.qcow2 --name debian --link

# List all base images
xray base list

# Remove a base image (fails if any VM depends on it)
xray base remove debian
```

### Creating base images from ISO

To create a new base image from an OS installation ISO on MacOS:

```bash
# 1. Create an empty qcow2 disk (e.g., 64GB)
qemu-img create -f qcow2 ~/ubuntu-base.qcow2 64G

# 2. Boot from ISO and install the OS
qemu-system-aarch64 \
  -accel hvf \
  -machine virt \
  -cpu host \
  -m 4096 \
  -smp 4 \
  -drive if=pflash,format=raw,readonly=on,file=/opt/homebrew/share/qemu/edk2-aarch64-code.fd \
  -drive if=pflash,format=raw,file=./ubuntu-efivars.fd \
  -drive if=virtio,format=qcow2,file=ubuntu-base.qcow2 \
  -drive if=none,id=cd,format=raw,file=ubuntu-25.10-desktop-arm64.iso \
  -device virtio-scsi-pci \
  -device scsi-cd,drive=cd,bootindex=1 \
  -device qemu-xhci \
  -device usb-kbd \
  -device usb-tablet \
  -device virtio-gpu-pci \
  -device virtio-net-pci,netdev=net0 \
  -netdev user,id=net0 \
  -display cocoa

# 3. Complete the OS installation, then shut down the VM

# 4. (Optional) Boot the installed system to configure it
qemu-system-aarch64 \
  -accel hvf \
  -machine virt \
  -cpu host \
  -m 4096 \
  -smp 4 \
  -drive if=pflash,format=raw,readonly=on,file=/opt/homebrew/share/qemu/edk2-aarch64-code.fd \
  -drive if=pflash,format=raw,file=/opt/homebrew/share/qemu/edk2-arm-vars.fd \
  -drive if=virtio,format=qcow2,file=~/ubuntu-base.qcow2 \
  -device qemu-xhci \
  -device usb-kbd \
  -device usb-tablet \
  -device virtio-gpu-pci \
  -device virtio-net-pci,netdev=net0 \
  -netdev user,id=net0 \
  -display cocoa

# 5. Import the configured image as a base
xray base import ~/ubuntu-base.qcow2 --name ubuntu
```

**Tips for creating base images:**
- Install cloud-init or configure SSH for easier VM access
- Install any common packages you'll need across VMs
- Update the system before shutting down
- Consider creating a user account with a known password
- The base image becomes read-only once imported, so configure everything you need first

**Pre-built images:**
- Ubuntu Cloud Images: https://cloud-images.ubuntu.com/ (look for ARM64 UEFI images)
- Debian Cloud Images: https://cloud.debian.org/images/cloud/
- Fedora Cloud: https://fedoraproject.org/cloud/download

Most cloud images work with xray — just download the qcow2 file and import it.

## Creating VMs

```bash
# Specify the base image (SSH port is auto-assigned starting from 2222)
xray create my-vm --base ubuntu
# Output: Created VM: my-vm (base: ubuntu)
#         SSH port: 2222 (ssh -p 2222 ubuntu@localhost)

# If you omit --base, you'll get an interactive picker
xray create my-vm

# Customize resources
xray create my-vm --base ubuntu --memory 8192 --cpus 8

# Add additional port forwards
xray create my-vm --base ubuntu --port 8080:80 --port 3000:3000

# Create and immediately start the VM
xray create my-vm --base ubuntu --start
```

Each VM automatically gets a unique SSH port assigned (starting from 2222). Use `xray list` to see all VMs and their SSH ports.

VM creation is near-instant because it only creates a small qcow2 overlay file that references the base image. No data is copied.

## Starting and stopping VMs

```bash
# Start a VM (runs in foreground, Ctrl+C or close window to stop)
xray start my-vm

# Start headless (no graphical window)
xray start my-vm --display none

# Skip running lifecycle hooks
xray start my-vm --no-hooks
```

## Listing and inspecting VMs

```bash
# List all VMs with their status
xray list

# Detailed info: config, disk chain, snapshots
xray info my-vm
```

## Snapshots

Snapshots work on both running and stopped VMs:

- **Running VM** — snapshot is taken live via the QEMU monitor (includes memory state, so reverting restores the exact running state)
- **Stopped VM** — snapshot is taken via `qemu-img` (disk state only)

```bash
# Create a snapshot
xray snapshot create my-vm before-update

# List all snapshots
xray snapshot list my-vm

# Revert to a snapshot
xray snapshot revert my-vm before-update

# Delete a snapshot
xray snapshot delete my-vm before-update
```

## Deleting VMs

```bash
# Delete a VM (asks for confirmation)
xray remove my-vm

# Skip confirmation
xray remove my-vm --force
```

## Network Firewall

xray includes a built-in network firewall that monitors all outgoing connections from VMs. When a VM tries to connect to an external service, you'll see a macOS notification asking whether to allow or deny the connection. Your decisions are stored and automatically applied to future connections.

**Features:**
- **Block-all by default** — All outgoing connections are blocked unless explicitly allowed
- **Smart notifications** — Shows hostname, IP, port, and service name (HTTP, HTTPS, etc.)
- **Default allowed domains** — Common services (Ubuntu repos, GitHub, PyPI, etc.) are auto-allowed
- **Per-VM rules** — Each VM has its own firewall configuration
- **Persistent rules** — Decisions are saved in `vm.toml` and auto-applied
- **Customizable defaults** — Edit `~/.xray/default-firewall-rules.conf` to add your own trusted domains

### Quick Setup

```bash
# 1. Create a VM (SSH port is auto-assigned)
xray create my-vm --base ubuntu

# 2. Start the VM (firewall is configured automatically on first boot)
xray start my-vm

# Done! All outgoing connections now require approval.
```

### How It Works

1. **Host-side proxy**: When you start a VM, xray launches a SOCKS5 proxy server on the host
2. **Automatic guest configuration**: On first boot, the `00-setup-firewall.sh` hook installs `redsocks` in the guest and configures iptables to route all TCP traffic through the proxy
3. **Connection interception**: When the VM tries to connect anywhere, the proxy checks firewall rules
4. **User approval**: If no rule exists, a macOS notification asks you to allow or deny
5. **Rule persistence**: Your decision is saved in `vm.toml` and auto-applied next time

### Managing Firewall Rules

```bash
# Check firewall status
xray firewall status my-vm

# List firewall rules for a VM
xray firewall list my-vm

# Manually add a rule
xray firewall add my-vm 1.1.1.1:443 allow
xray firewall add my-vm 10.0.0.1:22 deny

# Remove a specific rule
xray firewall remove my-vm 1.1.1.1:443

# Clear all rules for a VM
xray firewall clear my-vm
```

### Example Workflow

```bash
# Start a VM with firewall configured
xray start my-vm

# Inside the VM, try to connect to an external service
curl https://example.com

# A macOS notification appears with detailed info:
# ┌─────────────────────────────────────────────┐
# │ xray Firewall                               │
# ├─────────────────────────────────────────────┤
# │ VM 'my-vm' wants to connect to:             │
# │                                             │
# │ Host: www.example.com                       │
# │ Address: 93.184.216.34:443                  │
# │ Service: HTTPS                              │
# │                                             │
# │ Allow this connection?                      │
# │                        [Deny] [Allow]       │
# └─────────────────────────────────────────────┘

# Click "Allow" — the connection succeeds and the rule is saved

# Try the same connection again
curl https://example.com

# This time it works immediately without a notification (rule auto-applied)

# Common services like GitHub and Ubuntu repos are auto-allowed:
apt update  # Works without notifications (canonical.com is in defaults)
git clone https://github.com/user/repo  # Also auto-allowed

# Check the stored rules
xray firewall list my-vm
```

### Configuration Storage

Firewall rules are stored in each VM's `vm.toml` file:

```toml
base = "ubuntu"
memory = 4096
cpus = 4
firewall_configured = true

[firewall]
"1.1.1.1:443" = "allow"
"8.8.8.8:53" = "allow"
"10.0.0.1:22" = "deny"
```

### Default Allowed Domains

To reduce notification noise, xray auto-allows connections to common trusted domains. These defaults are stored in `~/.xray/default-firewall-rules.conf`:

```conf
# Default allowed domains for xray firewall
# Lines starting with # are comments
# Each line should be a domain suffix to allow (e.g., "github.com" allows *.github.com)

# Ubuntu package repositories
archive.ubuntu.com
ports.ubuntu.com
security.ubuntu.com

# Canonical services (NTP, mirrors, etc.)
canonical.com

# Development services
github.com
pypi.org
npmjs.org
```

**How domain matching works:**
- Each line is a domain suffix
- `github.com` matches `github.com` and `*.github.com` (e.g., `api.github.com`)
- Matching is done via reverse DNS lookup on the destination IP

**Customizing defaults:**
```bash
# Edit the defaults file
nano ~/.xray/default-firewall-rules.conf

# Add your company's internal domains
echo "internal.mycompany.com" >> ~/.xray/default-firewall-rules.conf

# Remove a default (just delete the line or comment it out with #)
```

The file is created automatically on first run with sensible defaults for Ubuntu development environments.

### Requirements

For the firewall to work, your VM needs:
- **SSH access**: Port forwarding configured (e.g., `--ssh-port 2222`)
- **Ubuntu/Debian**: The setup script uses `apt` to install `redsocks`
- **SSH key auth or password**: xray needs to SSH into the VM

### Security Considerations

- **Default policy:** Block all — maximum security, but requires configuring rules for every service
- **Per-VM isolation:** Rules are not shared between VMs
- **Guest configuration:** The setup installs `redsocks` and iptables rules in the guest
- **Version control friendly:** Rules are stored in TOML format, can be committed to git
- **No root on host:** Only needs SSH access to guest

### Troubleshooting

**"No SSH port configured" error:**
```bash
# Add SSH port forwarding
xray port add my-vm 2222:22
# Restart VM for port to take effect
xray stop my-vm && xray start my-vm
```

**SSH authentication fails:**
- Ensure your SSH key is in the VM's `~/.ssh/authorized_keys`
- Or configure your VM's SSH user in the hook scripts

**Notification doesn't appear:**
- Ensure System Preferences → Notifications → Script Editor is enabled
- The notification times out after 5 minutes and defaults to "deny"

**Guest can still connect without approval:**
- Reset initial-boot and restart: `xray hooks reset-initial-boot my-vm && xray stop my-vm && xray start my-vm`
- Check that redsocks is running in the guest: `systemctl status redsocks`

## Lifecycle Hooks

xray supports running scripts at different points in a VM's lifecycle. Scripts run **on the host** and receive environment variables with VM connection details, allowing you to SSH/SCP into the guest as needed.

### Hook Types

- **initial-boot**: Run once on first boot (tracked via `first_boot_completed` flag in vm.toml)
- **boot**: Run every time the VM starts

### Script Locations

Scripts are merged from three sources (in execution order):

1. **xray built-in** — `<xray-package>/scripts/{hook_type}/`
2. **User global** — `~/.xray/scripts/{hook_type}/`
3. **Per-VM** — `~/.xray/vms/{vm}/scripts/{hook_type}/`

Scripts within each source run in alphabetical order.

### Environment Variables

Scripts receive these environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `XRAY_VM_NAME` | VM name | `my-vm` |
| `XRAY_SSH_PORT` | SSH port on localhost | `2222` |
| `XRAY_SSH_USER` | SSH username | `ubuntu` |
| `XRAY_SSH_HOST` | SSH host (always localhost) | `127.0.0.1` |

### Example Hook Script

Create a file at `~/.xray/scripts/boot/install-tools.sh`:

```bash
#!/bin/bash
# Install common development tools on every boot

ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -p "$XRAY_SSH_PORT" "$XRAY_SSH_USER@$XRAY_SSH_HOST" \
    'sudo apt-get update && sudo apt-get install -y htop vim git'

echo "Tools installed in $XRAY_VM_NAME"
```

Make it executable:
```bash
chmod +x ~/.xray/scripts/boot/install-tools.sh
```

### Managing Hooks

```bash
# Initialize scripts directories
xray hooks init

# Initialize directories for a specific VM too
xray hooks init my-vm

# List all hooks that will run for a VM
xray hooks list my-vm

# Manually run hooks (VM must be running)
xray hooks run my-vm boot

# Reset initial-boot flag (so initial-boot hooks run again)
xray hooks reset-initial-boot my-vm

# Start VM without running hooks
xray start my-vm --no-hooks
```

### Use Cases

- **Initial setup**: Install packages, configure services on first boot
- **Development environment**: Mount shared folders, set up SSH keys
- **Testing**: Reset test data, start services after boot
- **Monitoring**: Log boot times, send notifications

## Configuration

### Global config

xray has a global configuration file at `~/.xray/config.toml` for settings that apply across all VMs.

```bash
# Show current config
xray config show

# Set a value
xray config set autostart true

# Print config file path
xray config path
```

#### Available settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `autostart` | bool | `false` | Automatically start a VM after `xray create` |

The `autostart` setting can be overridden per-invocation with the `--start` / `--no-start` flag on `xray create`:

```bash
# Global autostart is off, but start this one VM after creation
xray create my-vm --base ubuntu --start

# Global autostart is on, but skip it for this VM
xray create my-vm --base ubuntu --no-start
```

### Storage

By default, xray stores everything in `~/.xray/`. Override with the `XRAY_HOME` environment variable:

```bash
export XRAY_HOME=/data/vms
```

Storage layout:

```
~/.xray/
├── config.toml                  # Global configuration
├── default-firewall-rules.conf  # Default allowed domains for firewall
├── scripts/                     # User global hook scripts
│   ├── initial-boot/
│   └── boot/
├── bases/                       # Immutable base images
│   └── ubuntu.qcow2
└── vms/
    └── my-vm/
        ├── vm.toml      # VM config (base, memory, cpus, firewall rules)
        ├── disk.qcow2   # Copy-on-write overlay
        ├── scripts/     # Per-VM hook scripts
        │   ├── initial-boot/
        │   └── boot/
        ├── qmp.sock     # QEMU monitor socket (while running)
        └── pid          # Process ID file (while running)
```
## Architecture notes

- Uses `qemu-system-aarch64` with Apple HVF acceleration for near-native performance on Apple Silicon
- UEFI boot via EDK2 firmware (auto-detected from Homebrew's qemu package)
- Each VM gets a QMP (QEMU Monitor Protocol) unix socket for management — this enables live snapshots and graceful ACPI shutdown
- Overlay images use relative paths to their backing file, so the entire `~/.xray` directory is portable
