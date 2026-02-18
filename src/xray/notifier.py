"""macOS notification system for xray firewall alerts."""

from __future__ import annotations

import socket
import subprocess

from . import config


# Common port to service name mapping
COMMON_PORTS = {
    20: "FTP Data",
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    110: "POP3",
    143: "IMAP",
    443: "HTTPS",
    465: "SMTPS",
    587: "SMTP Submission",
    993: "IMAPS",
    995: "POP3S",
    3306: "MySQL",
    5432: "PostgreSQL",
    6379: "Redis",
    8080: "HTTP Proxy",
    8443: "HTTPS Alt",
    27017: "MongoDB",
}


def _get_hostname(ip: str) -> str | None:
    """Try to get hostname for an IP via reverse DNS lookup."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except (socket.herror, socket.gaierror, OSError):
        return None


def _get_service_name(port: int) -> str | None:
    """Get a human-readable service name for a port."""
    return COMMON_PORTS.get(port)


def _escape_applescript(s: str) -> str:
    """Escape a string for safe inclusion in AppleScript double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _format_destination(
    dest_ip: str,
    dest_port: int,
    domain: str | None = None,
    process_name: str | None = None,
) -> str:
    """Format destination with hostname, domain, process, and service info."""
    lines = []

    # Domain from DNS interception (preferred) or rDNS fallback
    if domain:
        lines.append(f"Domain: {_escape_applescript(domain)}")
    else:
        hostname = _get_hostname(dest_ip)
        if hostname:
            lines.append(f"Host: {_escape_applescript(hostname)}")

    # Add IP:port
    lines.append(f"Address: {dest_ip}:{dest_port}")

    # Add service name if known
    service = _get_service_name(dest_port)
    if service:
        lines.append(f"Service: {service}")

    # Add process info if available
    if process_name:
        lines.append(f"Process: {_escape_applescript(process_name)}")

    return "\" & return & \"".join(lines)


def _format_recent(recent_connections: list) -> str:
    """Format recent connection decisions for the dialog.

    Args:
        recent_connections: List of ConnectionRecord objects with
            domain, dest_ip, dest_port, and decision attributes.

    Returns:
        Formatted string for AppleScript, or empty string if no records.
    """
    if not recent_connections:
        return ""

    lines = []
    for rec in recent_connections:
        prefix = "+" if rec.decision == "allow" else "-"
        target = rec.domain or rec.dest_ip
        target = _escape_applescript(target)
        lines.append(f"  {prefix} {target}:{rec.dest_port} ({rec.decision})")

    # Build AppleScript string fragments that continue from _format_destination.
    # Each '" & return & "' closes the current string, adds a newline, and opens a new one.
    ret = '" & return & "'
    # Extra blank line before "Recent:" header
    parts = [ret + ret + "Recent:"]
    for line in lines:
        parts.append(ret + line)
    return "".join(parts)


def show_firewall_alert(
    vm_name: str,
    dest_ip: str,
    dest_port: int,
    domain: str | None = None,
    process_name: str | None = None,
    recent_connections: list | None = None,
) -> str:
    """Show a macOS notification asking to allow/deny a connection.

    Uses osascript to show a dialog with Allow/Deny buttons.

    Args:
        vm_name: Name of the VM
        dest_ip: Destination IP address
        dest_port: Destination port
        domain: Domain name from DNS interception (if available)
        process_name: Guest process name (if available)
        recent_connections: Recent ConnectionRecord objects for context

    Returns:
        "allow" or "deny" based on user choice
    """
    # Format destination with additional info
    dest_info = _format_destination(dest_ip, dest_port, domain=domain, process_name=process_name)

    # Format recent connections section
    recent_section = _format_recent(recent_connections or [])

    # Escape VM name for AppleScript
    safe_vm_name = _escape_applescript(vm_name)

    # Use osascript to show a dialog
    # Activate Terminal first to ensure the dialog appears in front
    script = f'''
    do shell script "afplay /System/Library/Sounds/Funk.aiff &"
    tell application "Terminal"
        activate
    end tell
    delay 0.1
    display dialog "VM '{safe_vm_name}' wants to connect to:" & return & return & "{dest_info}{recent_section}" & return & return & "Allow this connection?" ¬
        buttons {{"Deny", "Allow"}} ¬
        default button "Deny" ¬
        with title "xray Firewall" ¬
        with icon caution ¬
        giving up after 300
    '''

    try:
        verbose = config.is_verbose()
        if verbose:
            print(f"[notifier] Showing alert for {vm_name} -> {dest_ip}:{dest_port}")
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        if verbose:
            print(f"[notifier] osascript returned: stdout={result.stdout!r}, stderr={result.stderr!r}, rc={result.returncode}")

        # osascript returns "button returned:Allow" or "button returned:Deny"
        # "gave up:true" means timeout
        if "gave up:true" in result.stdout:
            if verbose:
                print(f"[notifier] Dialog timed out - defaulting to deny")
            return "deny"
        elif "Allow" in result.stdout:
            if verbose:
                print(f"[notifier] User chose ALLOW")
            return "allow"
        else:
            if verbose:
                print(f"[notifier] User chose DENY (or dialog was cancelled)")
            return "deny"

    except subprocess.TimeoutExpired:
        # Default to deny if user doesn't respond
        if config.is_verbose():
            print(f"[notifier] Timeout - defaulting to deny")
        return "deny"
    except Exception as e:
        # Default to deny on error (always visible)
        print(f"[notifier] Error showing notification: {e}")
        return "deny"


def show_notification(title: str, message: str) -> None:
    """Show a simple macOS notification (non-blocking).

    Args:
        title: Notification title
        message: Notification message
    """
    script = f'''
    display notification "{message}" with title "{title}"
    '''

    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Ignore errors for non-blocking notifications
