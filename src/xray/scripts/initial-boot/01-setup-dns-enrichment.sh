#!/bin/bash
# xray DNS enrichment setup script (runs on HOST)
# Installs dnsmasq for DNS logging, conntrack for process tracking,
# and deploys the xray-enrich script to the guest VM.

set -e

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

run_ssh() {
    ssh $SSH_OPTS -p "$XRAY_SSH_PORT" "$XRAY_SSH_USER@$XRAY_SSH_HOST" "$@"
}

echo "Setting up DNS enrichment in guest..."

# Install dnsmasq and conntrack
echo "  Installing dnsmasq and conntrack..."
run_ssh 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dnsmasq conntrack'

# Write dnsmasq config for DNS logging
echo "  Configuring dnsmasq..."
run_ssh 'sudo mkdir -p /etc/dnsmasq.d
cat > /tmp/xray-dnsmasq.conf << '\''EOF'\''
# xray DNS enrichment config
# Forward to QEMU user-mode DNS
server=10.0.2.3
# Only listen on localhost
listen-address=127.0.0.1
bind-interfaces
# Log all queries for domain enrichment
log-queries
log-facility=/var/log/xray-dns.log
# Cache settings
cache-size=1000
no-resolv
no-hosts
EOF
sudo mv /tmp/xray-dnsmasq.conf /etc/dnsmasq.d/xray.conf'

# Disable systemd-resolved (conflicts on port 53)
echo "  Disabling systemd-resolved..."
run_ssh 'sudo systemctl disable --now systemd-resolved 2>/dev/null || true'

# Set resolv.conf to use local dnsmasq and protect from overwrite.
# chattr may not be supported, so also disable dhclient/NM from touching it.
echo "  Configuring resolv.conf..."
run_ssh 'sudo chattr -i /etc/resolv.conf 2>/dev/null || true
echo "nameserver 127.0.0.1" | sudo tee /etc/resolv.conf > /dev/null
sudo chattr +i /etc/resolv.conf 2>/dev/null || true
# Tell dhclient not to overwrite resolv.conf
if [ -d /etc/dhcp/dhclient-enter-hooks.d ]; then
    echo "make_resolv_conf() { :; }" | sudo tee /etc/dhcp/dhclient-enter-hooks.d/xray-nodns > /dev/null
    sudo chmod +x /etc/dhcp/dhclient-enter-hooks.d/xray-nodns
fi
# Tell NetworkManager to leave DNS alone
if [ -d /etc/NetworkManager/conf.d ]; then
    printf "[main]\ndns=none\n" | sudo tee /etc/NetworkManager/conf.d/xray-dns.conf > /dev/null
fi'

# Create DNS log file readable by all (dnsmasq would create it root-only)
echo "  Preparing DNS log..."
run_ssh 'sudo touch /var/log/xray-dns.log && sudo chmod 644 /var/log/xray-dns.log'

# Create logrotate config for DNS log
echo "  Setting up log rotation..."
run_ssh 'cat > /tmp/xray-dns-logrotate << '\''EOF'\''
/var/log/xray-dns.log {
    daily
    rotate 3
    maxsize 10M
    missingok
    notifempty
    copytruncate
    create 644 root root
}
EOF
sudo mv /tmp/xray-dns-logrotate /etc/logrotate.d/xray-dns'

# Enable and start dnsmasq
echo "  Starting dnsmasq..."
run_ssh 'sudo systemctl enable dnsmasq && sudo systemctl restart dnsmasq'

# Deploy the xray-enrich script
echo "  Deploying xray-enrich script..."
run_ssh 'cat > /tmp/xray-enrich << '\''ENRICHEOF'\''
#!/bin/bash
# xray-enrich: Look up domain name and process info for a connection
# Usage: xray-enrich <dest_ip> <dest_port>
# Output: key=value pairs on stdout, debug traces on stderr

DEST_IP="$1"; DEST_PORT="$2"

# DNS: grep dnsmasq log for "reply <domain> is <IP>"
if [ -f /var/log/xray-dns.log ]; then
    DOMAIN=$(sudo tac /var/log/xray-dns.log | grep -m1 " is ${DEST_IP}$" | sed '\''s/.*reply \(.*\) is .*/\1/'\'')
    [ -n "$DOMAIN" ] && echo "domain=$DOMAIN"
fi

# Process: conntrack -> source port -> ss -> process name
CT_LINE=$(sudo conntrack -L -p tcp --dst "$DEST_IP" --dport "$DEST_PORT" 2>/dev/null | head -1)
echo "conntrack: ${CT_LINE:-(empty)}" >&2
SPORT=$(echo "$CT_LINE" | grep -o '\''sport=[0-9]*'\'' | head -1 | cut -d= -f2)

if [ -z "$SPORT" ]; then
    NF_LINE=$(sudo grep "dst=${DEST_IP} " /proc/net/nf_conntrack 2>/dev/null | grep "dport=${DEST_PORT} " | head -1)
    echo "nf_conntrack: ${NF_LINE:-(empty)}" >&2
    SPORT=$(echo "$NF_LINE" | grep -o '\''sport=[0-9]*'\'' | head -1 | cut -d= -f2)
fi

echo "sport: ${SPORT:-(empty)}" >&2

if [ -n "$SPORT" ]; then
    LINE=$(sudo ss -tnp "sport = :${SPORT}" | grep "127.0.0.1:12345" | head -1)
    echo "ss: ${LINE:-(empty)}" >&2
    PNAME=$(echo "$LINE" | grep -oP '\''users:\(\("\K[^"]+'\'' 2>/dev/null)
    PID=$(echo "$LINE" | grep -oP '\''pid=\K[0-9]+'\'' 2>/dev/null)
    [ -n "$PNAME" ] && echo "process_name=$PNAME"
    [ -n "$PID" ] && echo "process_pid=$PID"
fi
exit 0
ENRICHEOF
sudo mv /tmp/xray-enrich /usr/local/bin/xray-enrich
sudo chmod +x /usr/local/bin/xray-enrich'

echo "DNS enrichment configured successfully!"
