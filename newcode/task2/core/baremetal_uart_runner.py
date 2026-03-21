from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.qemu_manager import QemuManager


def _normalize_uart_input(text: str) -> bytes:
    # Protocol: newline (`\n`) is the input end marker.
    # We enforce that the input ends with '\n' so the firmware can reliably
    # detect the end via its newline+idle EOF heuristic.
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    if not s.endswith("\n"):
        s += "\n"
    return s.encode("utf-8", errors="ignore")


def _get_free_local_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@dataclass(frozen=True)
class BareMetalRunResult:
    actual_output: str
    exec_time_ms: int
    recovery_success: Optional[bool]


class BareMetalUartRunner:
    """
    Run one-shot QEMU session for a single test point:
      - QEMU loads `firmware.bin`
      - UART1 RX receives `in.txt` via a TCP socket backend
      - UART1 TX outputs program stdout to the same TCP stream
      - we capture until UART becomes idle after last newline (or timeout)
      - optional: send `inject_error addr bit` to QEMU monitor before feeding input
    """

    def __init__(
        self,
        qemu_mgr: QemuManager,
        *,
        uart_host: str = "127.0.0.1",
    ):
        self.qemu_mgr = qemu_mgr
        self.uart_host = uart_host

    def run_once(
        self,
        firmware_bin_path: Path,
        input_text: str,
        *,
        logger: Callable[[str], None],
        inject_error_addr: Optional[int] = None,
        inject_error_bit: Optional[int] = None,
        recovery_event: Optional[threading.Event] = None,
        total_timeout_sec: float = 10.0,
        uart_connect_timeout_sec: float = 5.0,
        uart_output_idle_sec: float = 0.35,
        inject_delay_sec: float = 0.25,
    ) -> BareMetalRunResult:
        port = _get_free_local_port()
        uart_bytes = _normalize_uart_input(input_text)

        start_t = time.time()
        recovery_success: Optional[bool] = None

        # 1) Start QEMU (UART1 listens on `port`).
        self.qemu_mgr.start_qemu_baremetal(
            log_callback=logger,
            firmware_bin_path=firmware_bin_path,
            uart_host=self.uart_host,
            uart_port=port,
        )

        try:
            # 2) Connect to UART socket (TCP client).
            deadline = time.time() + uart_connect_timeout_sec
            conn: Optional[socket.socket] = None
            last_err: Optional[Exception] = None
            while time.time() < deadline:
                try:
                    conn = socket.create_connection((self.uart_host, port), timeout=1.0)
                    break
                except Exception as e:  # pragma: no cover (depends on runtime timing)
                    last_err = e
                    time.sleep(0.1)
            if conn is None:
                raise TimeoutError(f"UART socket connect timeout: {last_err}")

            # 3) Inject fault (optional) before feeding UART input.
            if inject_error_addr is not None and inject_error_bit is not None:
                time.sleep(inject_delay_sec)
                self.qemu_mgr.send_debug_command(
                    f"inject_error {inject_error_addr} {inject_error_bit}"
                )

            # 4) Feed UART input.
            conn.sendall(uart_bytes)
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                pass

            # 5) Capture UART output until idle.
            conn.settimeout(0.2)
            chunks = bytearray()
            last_recv_t = time.time()

            while True:
                # Hard deadline
                if time.time() - start_t > total_timeout_sec:
                    raise TimeoutError("UART capture timeout")

                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.extend(data)
                    last_recv_t = time.time()
                except socket.timeout:
                    # idle detection
                    if (time.time() - last_recv_t) >= uart_output_idle_sec:
                        break

            # 6) Stop QEMU after capturing output.
            #    For injection mode, wait a bit for ERROR_RECOVERED to appear.
            if recovery_event is not None:
                # If QEMU already logged it, event is set immediately.
                remaining = max(0.0, total_timeout_sec - (time.time() - start_t))
                recovery_success = recovery_event.wait(timeout=remaining)
            else:
                recovery_success = None

            out_text = chunks.decode("utf-8", errors="ignore")
            return BareMetalRunResult(
                actual_output=out_text,
                exec_time_ms=int((time.time() - start_t) * 1000),
                recovery_success=recovery_success,
            )
        finally:
            try:
                self.qemu_mgr.stop_qemu()
            except Exception:
                pass

