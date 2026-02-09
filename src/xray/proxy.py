"""SOCKS5 proxy server with firewall enforcement for xray VMs."""

from __future__ import annotations

import asyncio
import logging
import struct
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

# Suppress coroutine warnings on abrupt shutdown
warnings.filterwarnings("ignore", message="coroutine .* was never awaited")
warnings.filterwarnings("ignore", message="coroutine ignored GeneratorExit")

# Configure logging - only show warnings and above by default
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Suppress asyncio "Task was destroyed" warnings on shutdown
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Dedicated thread pool for firewall rule checks (which may show blocking notifications)
# We need at least 1 thread to process notifications, but only 1 to serialize them
_notification_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="firewall-check")


class SOCKS5Server:
    """Async SOCKS5 proxy server that enforces firewall rules."""

    def __init__(
        self,
        vm_name: str,
        host: str = "0.0.0.0",  # Bind to all interfaces so guest can reach via 10.0.2.2
        port: int = 1080,  # Fixed port so guest knows where to connect
        check_rule: Callable[[str, int], str | None] = None,
    ):
        """
        Args:
            vm_name: Name of the VM this proxy serves
            host: Host to bind to
            port: Port to bind to (0 = auto-assign)
            check_rule: Callback that checks if IP:port is allowed/denied/unknown
                        Returns: "allow", "deny", or None (prompt user)
        """
        self.vm_name = vm_name
        self.host = host
        self.port = port
        self.check_rule = check_rule or (lambda ip, port: None)
        self.server: asyncio.Server | None = None
        self._actual_port: int | None = None

    async def start(self) -> int:
        """Start the SOCKS5 server.

        Returns:
            The port the server is listening on.
        """
        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        # Get actual port if auto-assigned
        self._actual_port = self.server.sockets[0].getsockname()[1]
        print(f"[proxy] Firewall proxy listening on port {self._actual_port}", flush=True)
        return self._actual_port

    async def stop(self) -> None:
        """Stop the SOCKS5 server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a SOCKS5 client connection."""
        peer = writer.get_extra_info('peername')
        dest_writer = None
        try:
            # SOCKS5 greeting
            version = await reader.readexactly(1)
            if version != b"\x05":
                logger.debug(f"Connection from {peer}: not SOCKS5, closing")
                writer.close()
                await writer.wait_closed()
                return

            # Read authentication methods
            nmethods = await reader.readexactly(1)
            methods = await reader.readexactly(nmethods[0])

            # Respond: no authentication required
            writer.write(b"\x05\x00")
            await writer.drain()

            # Read connection request
            version, cmd, _, atyp = await reader.readexactly(4)

            if cmd != 0x01:  # Only support CONNECT
                writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # Command not supported
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Parse destination address
            if atyp == 0x01:  # IPv4
                addr_bytes = await reader.readexactly(4)
                dest_ip = ".".join(str(b) for b in addr_bytes)
            elif atyp == 0x03:  # Domain name
                domain_len = await reader.readexactly(1)
                domain = await reader.readexactly(domain_len[0])
                dest_ip = domain.decode("utf-8")
            else:
                writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")  # Address type not supported
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Parse destination port
            port_bytes = await reader.readexactly(2)
            dest_port = struct.unpack(">H", port_bytes)[0]

            # Check firewall rules - run in dedicated thread pool since check_rule may block on notification
            loop = asyncio.get_running_loop()
            decision = await loop.run_in_executor(_notification_executor, self.check_rule, dest_ip, dest_port)

            if decision == "deny":
                print(f"[firewall] {dest_ip}:{dest_port} DENIED", flush=True)
                writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")  # Connection not allowed
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
            elif decision == "allow":
                print(f"[firewall] {dest_ip}:{dest_port} ALLOWED", flush=True)
            else:
                # decision is None or unknown - deny by default
                print(f"[firewall] {dest_ip}:{dest_port} DENIED (no rule)", flush=True)
                writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")  # Connection not allowed
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Attempt to connect to destination
            try:
                dest_reader, dest_writer = await asyncio.open_connection(dest_ip, dest_port)
            except Exception as e:
                logger.error(f"Failed to connect to {dest_ip}:{dest_port}: {e}")
                writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")  # Connection refused
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Send success response
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()

            # Relay data bidirectionally
            await asyncio.gather(
                self._relay(reader, dest_writer),
                self._relay(dest_reader, writer),
                return_exceptions=True,
            )

        except asyncio.CancelledError:
            # Server is shutting down - this is expected
            pass
        except asyncio.IncompleteReadError:
            # Connection closed before full SOCKS5 handshake - this is normal
            # (e.g., connectivity check, client disconnect)
            logger.debug(f"Connection from {peer} closed during handshake")
        except Exception as e:
            logger.error(f"Error handling SOCKS5 connection from {peer}: {e}")
        finally:
            # Close destination connection if we opened one
            if dest_writer is not None:
                try:
                    dest_writer.close()
                    await dest_writer.wait_closed()
                except Exception:
                    pass
            # Close client connection
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Relay data from reader to writer."""
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except asyncio.CancelledError:
            # Shutdown - just exit
            pass
        except Exception:
            pass


async def run_proxy_for_vm(
    vm_name: str,
    check_rule_callback: Callable[[str, int], str | None],
) -> tuple[SOCKS5Server, int]:
    """Start a SOCKS5 proxy for a VM.

    Args:
        vm_name: Name of the VM
        check_rule_callback: Function to check firewall rules

    Returns:
        Tuple of (server instance, port number)
    """
    server = SOCKS5Server(vm_name, check_rule=check_rule_callback)
    port = await server.start()
    return server, port
