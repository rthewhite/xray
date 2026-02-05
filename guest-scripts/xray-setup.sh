#!/bin/bash
# xray-setup.sh - Setup xray VM environment
# This script runs at boot to configure the xray VM environment
# - Mounts virtio-9p shares from the xray host
# - Future: Configure network, install tools, setup SSH keys, etc.

set -e

# Log function
log() {
    echo "[xray-setup] $1"
    logger -t xray-setup "$1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log "ERROR: Must run as root"
    exit 1
fi

# Detect the primary user (first non-system user with home dir)
PRIMARY_USER=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 && $6 ~ /^\/home\// {print $1; exit}')
if [ -z "$PRIMARY_USER" ]; then
    log "WARNING: Could not detect primary user, skipping user-specific mounts"
else
    log "Detected primary user: $PRIMARY_USER"
    PRIMARY_HOME=$(getent passwd "$PRIMARY_USER" | cut -d: -f6)
fi

# Mount Claude credentials (always try this)
mount_claude() {
    local mount_point="${PRIMARY_HOME}/.claude"
    local mount_tag="claude_creds"

    log "Mounting Claude credentials..."

    # Create mount point if it doesn't exist
    if [ -n "$PRIMARY_USER" ] && [ -n "$PRIMARY_HOME" ]; then
        mkdir -p "$mount_point"
        chown "$PRIMARY_USER:$PRIMARY_USER" "$mount_point"

        # Check if already mounted
        if mountpoint -q "$mount_point"; then
            log "Claude credentials already mounted at $mount_point"
            return 0
        fi

        # Wait for virtio device to become available
        local max_wait=30
        local waited=0
        local device_ready=false

        log "Waiting for virtio device '$mount_tag' to become available..."
        while [ $waited -lt $max_wait ]; do
            # Check if the mount_tag appears in any virtio device
            if [ -d /sys/bus/virtio/drivers/9pnet_virtio ] && \
               find /sys/bus/virtio/drivers/9pnet_virtio/virtio* -name mount_tag -exec grep -q "^${mount_tag}$" {} \; 2>/dev/null; then
                log "Virtio device '$mount_tag' is ready"
                device_ready=true
                break
            fi

            if [ $waited -eq 0 ]; then
                log "Device not ready yet, waiting..."
            fi

            sleep 1
            waited=$((waited + 1))
        done

        if [ "$device_ready" = false ]; then
            log "WARNING: Virtio device '$mount_tag' did not become available within ${max_wait}s"
            return 1
        fi

        # Device is ready, attempt mount
        if mount -t 9p -o trans=virtio,version=9p2000.L,cache=loose "$mount_tag" "$mount_point" 2>/dev/null; then
            log "Successfully mounted Claude credentials at $mount_point"
            chown "$PRIMARY_USER:$PRIMARY_USER" "$mount_point"
            return 0
        else
            log "ERROR: Mount command failed even though device is ready"
            return 1
        fi
    else
        log "WARNING: Skipping Claude mount - no primary user detected"
        return 1
    fi
}

# Mount GitHub tokens (future enhancement)
mount_github() {
    # Placeholder for future GitHub token mounting
    # Uncomment when xray supports GitHub token sharing
    # local mount_point="${PRIMARY_HOME}/.github"
    # local mount_tag="github_tokens"
    # log "GitHub token mounting not yet implemented"
    :
}

# Main execution
log "Starting xray VM setup..."

# Load 9p kernel modules if not already loaded
if ! lsmod | grep -q 9pnet_virtio; then
    log "Loading 9p kernel modules..."
    modprobe 9pnet_virtio 2>/dev/null || log "WARNING: Could not load 9pnet_virtio module"
fi

# Mount all shares
mount_claude
# mount_github  # Uncomment when implemented

# Future enhancements:
# - Configure network settings
# - Install/update development tools
# - Setup SSH keys
# - Configure git
# - etc.

log "xray VM setup completed"
exit 0
