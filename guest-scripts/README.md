# xray Guest Scripts

Systemd service and scripts for auto-mounting xray shared directories in guest VMs.

## What it does

The `xray-setup` service automatically mounts virtio-9p shares from the xray host into your VM:

- **Claude credentials** (`~/.xray/.claude` on host → `~/.claude` in VM)
- **Future:** GitHub tokens, SSH keys, and other secrets

## Installation

### In your base image (recommended)

Before creating your base image, install these scripts:

```bash
# Clone xray repo or copy guest-scripts directory
git clone https://github.com/YOUR_USERNAME/xray.git
cd xray/guest-scripts

# Install (requires root)
sudo ./install.sh
```

The service will automatically start on every boot.

### In an existing VM

You can also install after the VM is created:

```bash
# Same steps as above
cd xray/guest-scripts
sudo ./install.sh

# Start immediately without reboot
sudo systemctl start xray-setup.service
```

## Files

- **`xray-setup.sh`** - Main mount script
  - Auto-detects primary user
  - Loads kernel modules
  - Mounts Claude credentials
  - Extensible for future mounts

- **`xray-setup.service`** - Systemd service unit
  - Runs at boot before multi-user.target
  - Logs to journald
  - Security hardened

- **`install.sh`** - Installation helper
  - Copies files to system locations
  - Enables systemd service
  - Makes setup easy

## Verification

Check if the service is running:

```bash
sudo systemctl status xray-setup.service
```

Check if Claude credentials are mounted:

```bash
ls -la ~/.claude
mountpoint ~/.claude
```

View logs:

```bash
sudo journalctl -u xray-setup.service
```

## Extending

To add more mounts (e.g., GitHub tokens), edit `xray-setup.sh`:

1. Add a new mount function (follow the `mount_claude()` pattern)
2. Call it from the main execution section
3. Update xray host to share the new directory via virtfs

## Requirements

- Linux with systemd
- 9p kernel modules (`9pnet_virtio`, `9pnet`, `9p`)
- Most modern distros include these by default

## Troubleshooting

### "Failed to mount" error

Check if 9p modules are loaded:
```bash
lsmod | grep 9p
```

Load manually if needed:
```bash
sudo modprobe 9pnet_virtio
```

### "No primary user detected"

The script looks for the first user with UID ≥ 1000 and a home in `/home/`.

Manually specify user by editing `/usr/local/bin/xray-setup.sh` and setting `PRIMARY_USER`.

### Mount point doesn't exist

The script creates it automatically, but if you have permission issues:
```bash
sudo mkdir -p ~/.claude
sudo chown $USER:$USER ~/.claude
```
