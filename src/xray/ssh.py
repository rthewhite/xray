"""SSH utilities for xray VMs."""

from __future__ import annotations

import socket
import subprocess
import time

# SSH options for VM connections
SSH_OPTIONS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
]


def wait_for_ssh(host: str, port: int, timeout: int = 120) -> bool:
    """Wait for SSH to become available on a host:port.

    This does a real SSH connection test, not just a port check.
    Returns True if SSH is available, False if timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        # Try a real SSH connection with a simple command
        try:
            result = subprocess.run(
                [
                    "ssh",
                    *SSH_OPTIONS,
                    "-p", str(port),
                    f"ubuntu@{host}",
                    "true",  # Simplest possible command
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        time.sleep(2)
    return False


def run_command(
    host: str,
    port: int,
    command: str,
    user: str = "ubuntu",
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a command via SSH.

    Returns (returncode, stdout, stderr).
    """
    ssh_cmd = [
        "ssh",
        *SSH_OPTIONS,
        "-p", str(port),
        f"{user}@{host}",
        command,
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "SSH command timed out"
    except Exception as e:
        return -1, "", str(e)


def copy_file(
    host: str,
    port: int,
    local_path: str,
    remote_path: str,
    user: str = "ubuntu",
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Copy a file to the VM via SCP.

    Returns (returncode, stdout, stderr).
    """
    scp_cmd = [
        "scp",
        *SSH_OPTIONS,
        "-P", str(port),
        local_path,
        f"{user}@{host}:{remote_path}",
    ]

    try:
        result = subprocess.run(
            scp_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "SCP command timed out"
    except Exception as e:
        return -1, "", str(e)


def run_script(
    host: str,
    port: int,
    script_content: str,
    user: str = "ubuntu",
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a bash script on the VM.

    Copies script to temp file, executes it, then removes it.
    Returns (returncode, stdout, stderr).
    """
    # Create a temp script path
    remote_script = "/tmp/xray_hook_script.sh"

    # Write script content using heredoc to handle special characters
    write_cmd = f"cat > {remote_script} << 'XRAY_HOOK_EOF'\n{script_content}\nXRAY_HOOK_EOF"

    rc, out, err = run_command(host, port, write_cmd, user=user, timeout=30)
    if rc != 0:
        return rc, out, f"Failed to write script: {err}"

    # Make executable and run
    exec_cmd = f"chmod +x {remote_script} && {remote_script}"
    rc, out, err = run_command(host, port, exec_cmd, user=user, timeout=timeout)

    # Cleanup (ignore errors)
    run_command(host, port, f"rm -f {remote_script}", user=user, timeout=10)

    return rc, out, err
