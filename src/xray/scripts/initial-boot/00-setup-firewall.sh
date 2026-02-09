#!/bin/bash
# xray firewall setup script (runs on HOST)
# Configures transparent proxy in the guest VM to route all TCP traffic through xray's firewall

set -e

# The proxy is exposed to guests via QEMU guestfwd
# Can't use 10.0.2.2 (gateway) - QEMU reserves it, use 10.0.2.100 instead
GUEST_PROXY_IP="10.0.2.100"
GUEST_PROXY_PORT="1080"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

run_ssh() {
    ssh $SSH_OPTS -p "$XRAY_SSH_PORT" "$XRAY_SSH_USER@$XRAY_SSH_HOST" "$@"
}

echo "Setting up firewall proxy in guest..."

# Install redsocks
echo "  Installing redsocks..."
run_ssh 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redsocks'

# Create redsocks config
echo "  Writing redsocks configuration..."
run_ssh "cat > /tmp/redsocks.conf << 'EOF'
base {
    log_debug = off;
    log_info = on;
    log = \"syslog:daemon\";
    daemon = on;
    redirector = iptables;
}

redsocks {
    local_ip = 127.0.0.1;
    local_port = 12345;
    ip = ${GUEST_PROXY_IP};
    port = ${GUEST_PROXY_PORT};
    type = socks5;
}
EOF
sudo mv /tmp/redsocks.conf /etc/redsocks.conf"

# Create iptables rules script
echo "  Setting up iptables rules..."
run_ssh 'cat > /tmp/redsocks-iptables.sh << '\''EOF'\''
#!/bin/bash
# Redirect all TCP traffic to redsocks (except local and to proxy itself)
iptables -t nat -N REDSOCKS 2>/dev/null || iptables -t nat -F REDSOCKS

# Don'\''t redirect local traffic
iptables -t nat -A REDSOCKS -d 0.0.0.0/8 -j RETURN
iptables -t nat -A REDSOCKS -d 10.0.0.0/8 -j RETURN
iptables -t nat -A REDSOCKS -d 127.0.0.0/8 -j RETURN
iptables -t nat -A REDSOCKS -d 169.254.0.0/16 -j RETURN
iptables -t nat -A REDSOCKS -d 172.16.0.0/12 -j RETURN
iptables -t nat -A REDSOCKS -d 192.168.0.0/16 -j RETURN
iptables -t nat -A REDSOCKS -d 224.0.0.0/4 -j RETURN
iptables -t nat -A REDSOCKS -d 240.0.0.0/4 -j RETURN

# Redirect everything else to redsocks
iptables -t nat -A REDSOCKS -p tcp -j REDIRECT --to-ports 12345

# Apply to OUTPUT chain
iptables -t nat -A OUTPUT -p tcp -j REDSOCKS
EOF
sudo mv /tmp/redsocks-iptables.sh /etc/redsocks-iptables.sh
sudo chmod +x /etc/redsocks-iptables.sh'

# Create systemd service to apply iptables on boot
echo "  Creating systemd service..."
run_ssh 'cat > /tmp/redsocks-iptables.service << '\''EOF'\''
[Unit]
Description=Redsocks iptables rules
After=network.target redsocks.service
Wants=redsocks.service

[Service]
Type=oneshot
ExecStart=/etc/redsocks-iptables.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/redsocks-iptables.service /etc/systemd/system/redsocks-iptables.service'

# Enable and start services
echo "  Enabling services..."
run_ssh 'sudo systemctl daemon-reload && sudo systemctl enable redsocks redsocks-iptables && sudo systemctl restart redsocks && sudo /etc/redsocks-iptables.sh'

echo "Firewall configured successfully!"
echo "All TCP traffic from '$XRAY_VM_NAME' now routes through the xray firewall."
