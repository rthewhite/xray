"""QMP (QEMU Monitor Protocol) socket client."""

from __future__ import annotations

import json
import socket
from pathlib import Path


class QMPError(Exception):
    pass


class QMPClient:
    """Simple QMP client for communicating with a running QEMU instance."""

    def __init__(self, sock_path: Path):
        self.sock_path = sock_path
        self._sock: socket.socket | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def connect(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(str(self.sock_path))
        except (ConnectionRefusedError, FileNotFoundError) as e:
            raise QMPError(f"Cannot connect to QMP socket: {e}") from e
        self._sock.settimeout(5.0)
        # Read the greeting
        self._recv()
        # Negotiate capabilities
        self._send({"execute": "qmp_capabilities"})
        resp = self._recv()
        if "error" in resp:
            raise QMPError(f"QMP negotiation failed: {resp['error']}")

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None

    def _send(self, data: dict):
        msg = json.dumps(data).encode() + b"\n"
        self._sock.sendall(msg)

    def _recv(self) -> dict:
        buf = b""
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise QMPError("Connection closed")
            buf += chunk
            try:
                return json.loads(buf)
            except json.JSONDecodeError:
                continue

    def execute(self, command: str) -> dict:
        """Execute a QMP command and return the response."""
        self._send({"execute": command})
        return self._recv()

    def human_command(self, cmd: str) -> str:
        """Execute a human monitor command and return the output string."""
        self._send({
            "execute": "human-monitor-command",
            "arguments": {"command-line": cmd},
        })
        resp = self._recv()
        if "error" in resp:
            raise QMPError(resp["error"].get("desc", str(resp["error"])))
        return resp.get("return", "")

    def savevm(self, name: str) -> str:
        return self.human_command(f"savevm {name}")

    def loadvm(self, name: str) -> str:
        return self.human_command(f"loadvm {name}")

    def delvm(self, name: str) -> str:
        return self.human_command(f"delvm {name}")

    def info_snapshots(self) -> str:
        return self.human_command("info snapshots")

    def quit(self) -> dict:
        return self.execute("quit")

    def system_powerdown(self) -> dict:
        """Send ACPI shutdown signal."""
        return self.execute("system_powerdown")
