#!/bin/bash
# xray DNS enrichment verification script (runs on HOST)
# Verifies dnsmasq and xray-enrich are set up in the guest.
# If not (e.g. VM was created before this feature), installs them inline.
# Always redeploys the xray-enrich script to pick up fixes.

set -e

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

run_ssh() {
    ssh $SSH_OPTS -p "$XRAY_SSH_PORT" "$XRAY_SSH_USER@$XRAY_SSH_HOST" "$@"
}

echo "Verifying DNS enrichment in guest..."

# Check if dnsmasq infrastructure needs setup
if ! run_ssh 'systemctl is-active dnsmasq >/dev/null 2>&1'; then
    echo "  DNS enrichment not fully set up, configuring..."

    # Install packages if needed
    if ! run_ssh 'which dnsmasq >/dev/null 2>&1'; then
        echo "  Installing dnsmasq and conntrack..."
        run_ssh 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dnsmasq conntrack'
    fi

    # Write dnsmasq config if missing
    if ! run_ssh 'test -f /etc/dnsmasq.d/xray.conf'; then
        echo "  Writing dnsmasq config..."
        run_ssh 'sudo mkdir -p /etc/dnsmasq.d
cat > /tmp/xray-dnsmasq.conf << '\''EOF'\''
server=10.0.2.3
listen-address=127.0.0.1
bind-interfaces
log-queries
log-facility=/var/log/xray-dns.log
cache-size=1000
no-resolv
no-hosts
EOF
sudo mv /tmp/xray-dnsmasq.conf /etc/dnsmasq.d/xray.conf'
    fi

    # Ensure systemd-resolved is disabled
    run_ssh 'sudo systemctl disable --now systemd-resolved 2>/dev/null || true'

    # Set resolv.conf and protect from overwrite
    run_ssh 'sudo chattr -i /etc/resolv.conf 2>/dev/null || true
echo "nameserver 127.0.0.1" | sudo tee /etc/resolv.conf > /dev/null
sudo chattr +i /etc/resolv.conf 2>/dev/null || true
if [ -d /etc/dhcp/dhclient-enter-hooks.d ]; then
    echo "make_resolv_conf() { :; }" | sudo tee /etc/dhcp/dhclient-enter-hooks.d/xray-nodns > /dev/null
    sudo chmod +x /etc/dhcp/dhclient-enter-hooks.d/xray-nodns
fi
if [ -d /etc/NetworkManager/conf.d ]; then
    printf "[main]\ndns=none\n" | sudo tee /etc/NetworkManager/conf.d/xray-dns.conf > /dev/null
fi'

    # Logrotate
    if ! run_ssh 'test -f /etc/logrotate.d/xray-dns'; then
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
    fi

    # Start dnsmasq
    run_ssh 'sudo systemctl enable dnsmasq && sudo systemctl restart dnsmasq'
fi

# Always ensure DNS log is readable (dnsmasq may have created it root-only)
run_ssh 'sudo touch /var/log/xray-dns.log && sudo chmod 644 /var/log/xray-dns.log'

# Always deploy latest xray-enrich script (picks up fixes)
run_ssh 'cat > /tmp/xray-enrich << '\''ENRICHEOF'\''
#!/bin/bash
DEST_IP="$1"; DEST_PORT="$2"
if [ -f /var/log/xray-dns.log ]; then
    DOMAIN=$(sudo tac /var/log/xray-dns.log | grep -m1 " is ${DEST_IP}$" | sed '\''s/.*reply \(.*\) is .*/\1/'\'')
    [ -n "$DOMAIN" ] && echo "domain=$DOMAIN"
fi
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

echo "DNS enrichment OK"
