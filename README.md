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
  -drive if=pflash,format=raw,file=/opt/homebrew/share/qemu/edk2-arm-vars.fd \
  -drive if=virtio,format=qcow2,file=~/ubuntu-base.qcow2 \
  -cdrom ~/Downloads/ubuntu-24.04-arm64.iso \
  -boot d \
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
# Specify the base image
xray create my-vm --base ubuntu

# If you omit --base, you'll get an interactive picker
xray create my-vm

# Customize resources
xray create my-vm --base ubuntu --memory 8192 --cpus 8

# Forward a port for SSH access
xray create my-vm --base ubuntu --ssh-port 2222
# Then: ssh -p 2222 user@localhost
```

VM creation is near-instant because it only creates a small qcow2 overlay file that references the base image. No data is copied.

## Starting and stopping VMs

```bash
# Start in background (default)
xray start my-vm

# Start in foreground (blocking, Ctrl+C to stop)
xray start my-vm --foreground

# Start with a graphical window
xray start my-vm --display sdl

# Start headless in background
xray start my-vm --display none

# Graceful shutdown (sends ACPI power button, waits up to 30s)
xray stop my-vm

# Force kill
xray stop my-vm --force
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

## Configuration

By default, xray stores everything in `~/.xray/`. Override with the `XRAY_HOME` environment variable:

```bash
export XRAY_HOME=/data/vms
```

Storage layout:

```
~/.xray/
├── bases/           # Immutable base images
│   └── ubuntu.qcow2
└── vms/
    └── my-vm/
        ├── vm.toml      # VM config (base, memory, cpus)
        ├── disk.qcow2   # Copy-on-write overlay
        ├── qmp.sock     # QEMU monitor socket (while running)
        └── pid           # Process ID file (while running)
```
## Architecture notes

- Uses `qemu-system-aarch64` with Apple HVF acceleration for near-native performance on Apple Silicon
- UEFI boot via EDK2 firmware (auto-detected from Homebrew's qemu package)
- Each VM gets a QMP (QEMU Monitor Protocol) unix socket for management — this enables live snapshots and graceful ACPI shutdown
- Overlay images use relative paths to their backing file, so the entire `~/.xray` directory is portable
