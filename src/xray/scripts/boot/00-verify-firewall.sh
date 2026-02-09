#!/bin/bash
# xray firewall verification script (runs on HOST)
# Verifies that redsocks and iptables rules are properly configured in the guest

set -e

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

run_ssh() {
    ssh $SSH_OPTS -p "$XRAY_SSH_PORT" "$XRAY_SSH_USER@$XRAY_SSH_HOST" "$@"
}

echo "Verifying firewall configuration in guest..."

ERRORS=0

# Check redsocks is installed
echo "  Checking redsocks installation..."
if ! run_ssh 'which redsocks >/dev/null 2>&1'; then
    echo "  ERROR: redsocks is not installed"
    ERRORS=$((ERRORS + 1))
fi

# Check redsocks config exists and has correct content
echo "  Checking redsocks configuration..."
if ! run_ssh 'test -f /etc/redsocks.conf'; then
    echo "  ERROR: /etc/redsocks.conf does not exist"
    ERRORS=$((ERRORS + 1))
else
    # Verify key settings in config
    if ! run_ssh 'grep -q "ip = 10.0.2.100" /etc/redsocks.conf'; then
        echo "  ERROR: redsocks.conf missing proxy IP (10.0.2.100)"
        ERRORS=$((ERRORS + 1))
    fi
    if ! run_ssh 'grep -q "port = 1080" /etc/redsocks.conf'; then
        echo "  ERROR: redsocks.conf missing proxy port (1080)"
        ERRORS=$((ERRORS + 1))
    fi
    if ! run_ssh 'grep -q "type = socks5" /etc/redsocks.conf'; then
        echo "  ERROR: redsocks.conf missing socks5 type"
        ERRORS=$((ERRORS + 1))
    fi
fi

# Check iptables script exists and is executable
echo "  Checking iptables script..."
if ! run_ssh 'test -x /etc/redsocks-iptables.sh'; then
    echo "  ERROR: /etc/redsocks-iptables.sh does not exist or is not executable"
    ERRORS=$((ERRORS + 1))
fi

# Check systemd services exist and are enabled
echo "  Checking systemd services..."
if ! run_ssh 'systemctl is-enabled redsocks >/dev/null 2>&1'; then
    echo "  ERROR: redsocks service is not enabled"
    ERRORS=$((ERRORS + 1))
fi
if ! run_ssh 'test -f /etc/systemd/system/redsocks-iptables.service'; then
    echo "  ERROR: redsocks-iptables.service does not exist"
    ERRORS=$((ERRORS + 1))
fi
if ! run_ssh 'systemctl is-enabled redsocks-iptables >/dev/null 2>&1'; then
    echo "  ERROR: redsocks-iptables service is not enabled"
    ERRORS=$((ERRORS + 1))
fi

# Check redsocks is running
echo "  Checking redsocks is running..."
if ! run_ssh 'systemctl is-active redsocks >/dev/null 2>&1'; then
    echo "  WARNING: redsocks is not running, attempting to start..."
    run_ssh 'sudo systemctl start redsocks' || true
    sleep 1
    if ! run_ssh 'systemctl is-active redsocks >/dev/null 2>&1'; then
        echo "  ERROR: Failed to start redsocks"
        ERRORS=$((ERRORS + 1))
    else
        echo "  redsocks started successfully"
    fi
fi

# Check iptables REDSOCKS chain exists and has rules
echo "  Checking iptables rules..."
if ! run_ssh 'sudo iptables -t nat -L REDSOCKS >/dev/null 2>&1'; then
    echo "  WARNING: REDSOCKS iptables chain does not exist, applying rules..."
    run_ssh 'sudo /etc/redsocks-iptables.sh' || true
    if ! run_ssh 'sudo iptables -t nat -L REDSOCKS >/dev/null 2>&1'; then
        echo "  ERROR: Failed to create REDSOCKS iptables chain"
        ERRORS=$((ERRORS + 1))
    else
        echo "  iptables rules applied successfully"
    fi
else
    # Verify OUTPUT chain has REDSOCKS jump
    if ! run_ssh 'sudo iptables -t nat -L OUTPUT | grep -q REDSOCKS'; then
        echo "  WARNING: OUTPUT chain missing REDSOCKS jump, applying rules..."
        run_ssh 'sudo /etc/redsocks-iptables.sh' || true
    fi
fi

# Final status
if [ $ERRORS -eq 0 ]; then
    echo "Firewall verification passed!"
else
    echo "Firewall verification found $ERRORS error(s)"
    echo "Run 'xray hooks reset-initial-boot $XRAY_VM_NAME' and restart to reconfigure"
    exit 1
fi
