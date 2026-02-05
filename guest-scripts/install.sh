#!/bin/bash
# install.sh - Install xray guest scripts on a VM
# Run this inside your base image before creating it

set -e

echo "==> Installing xray guest scripts..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must run as root (use sudo)"
    exit 1
fi

# Detect script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install the setup script
echo "==> Installing xray-setup.sh to /usr/local/bin/"
install -m 755 "$SCRIPT_DIR/xray-setup.sh" /usr/local/bin/xray-setup.sh

# Install the systemd service
echo "==> Installing xray-setup.service to /etc/systemd/system/"
install -m 644 "$SCRIPT_DIR/xray-setup.service" /etc/systemd/system/xray-setup.service

# Reload systemd
echo "==> Reloading systemd daemon..."
systemctl daemon-reload

# Enable the service
echo "==> Enabling xray-setup.service..."
systemctl enable xray-setup.service

# Test the service (optional)
echo ""
echo "==> Installation complete!"
echo ""
echo "To test the setup now, run:"
echo "  sudo systemctl start xray-setup.service"
echo "  sudo systemctl status xray-setup.service"
echo ""
echo "To check if Claude credentials are mounted:"
echo "  ls -la ~/.claude"
echo ""
echo "The service will automatically run on boot."
