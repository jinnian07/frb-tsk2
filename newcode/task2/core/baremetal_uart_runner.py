from __future__ import annotations

import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.fault_injection_config import gdb_command_for_subprocess, inject_after_uart_input
from core.qemu_manager import QemuManager
from core.stack_watermark import (
    collect_stack_watermark,
    enrich_stack_watermark_result,
    format_watermark_summary,
    merge_stack_watermark_config,
)

# 固件经 USART1 打印的恢复标记；仅匹配 UART 字节流（不会出现在 QEMU 子进程 stdout）。
_RECOVERY_UART_MARK = b"ERROR_RECOVERED"


_HEX_TOKEN = re.compile(r"^[0-9A-Fa-f]{2}$")


def _try_parse_hex_byte_stream(text: str) -> Optional[bytes]:
    """
    If every whitespace-separated token is exactly one byte in hex (two digits),
    return those bytes. Otherwise None — caller keeps UTF-8 text mode (scanf I/O).
    """
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = s.split()
    if not parts:
        return None
    out = bytearray()
    for p in parts:
        if not _HEX_TOKEN.match(p):
            return None
        out.append(int(p, 16))
    return bytes(out)


def _normalize_uart_input(text: str) -> bytes:
    """
    Build UART RX payload for bare-metal tests.

    - If the whole input (tokens separated by whitespace) is hex-bytes only, send
      raw bytes (P0002-style ``AA 03 11 ...``).
    - Otherwise UTF-8 encode the string (legacy scanf/stdin-style problems).
    - Append ``\\n`` if missing for newlib _read() EOF; 0x0A in IDLE is ignored by
      P0002 frame sync.
    """
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    hex_bytes = _try_parse_hex_byte_stream(s)
    if hex_bytes is not None:
        raw = hex_bytes
    else:
        raw = s.encode("utf-8", errors="ignore")
    if not raw.endswith(b"\n"):
        raw += b"\n"
    return raw


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
    # 堆栈水位线（GDB dump + 栈染色）；失败时含 error 字段
    stack_watermark: Optional[Dict[str, Any]] = None


class BareMetalUartRunner:
    """
    Run one-shot QEMU session for a single test point:
      - QEMU loads `firmware.bin`
      - UART1 RX receives `in.txt` via a TCP socket backend
      - UART1 TX outputs program stdout to the same TCP stream
      - we capture until UART becomes idle after last newline (or timeout)
      - optional: GDB（gdbstub）对 SRAM 字做异或位翻转；时机由 fault_injection_config.inject_after_uart_input
        决定（默认在 sendall 输入之后，inject_delay_sec 为发送后的等待）
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
        firmware_elf_path: Optional[Path] = None,
        stack_watermark_cfg: Optional[dict] = None,
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

            fc = getattr(self.qemu_mgr, "_fault_config", None) or {}
            need_inject = (
                inject_error_addr is not None and inject_error_bit is not None
            )
            after_in = inject_after_uart_input(fc) if need_inject else True

            # 3) 可选：旧顺序 — 发输入前先翻转（对照实验）
            if need_inject and not after_in:
                time.sleep(inject_delay_sec)
                self.qemu_mgr.apply_gdb_memory_bitflip(
                    inject_error_addr, inject_error_bit
                )

            # 4) Feed UART input.
            conn.sendall(uart_bytes)
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                pass

            # 5) 可选：默认 — 发输入后再翻转，再给程序时间反应
            if need_inject and after_in:
                time.sleep(inject_delay_sec)
                self.qemu_mgr.apply_gdb_memory_bitflip(
                    inject_error_addr, inject_error_bit
                )

            # 6) Capture UART output until idle.
            conn.settimeout(0.2)
            chunks = bytearray()
            last_recv_t = time.time()

            def _touch_recovery_from_uart() -> None:
                if recovery_event is not None and _RECOVERY_UART_MARK in chunks:
                    recovery_event.set()

            while True:
                # Hard deadline
                if time.time() - start_t > total_timeout_sec:
                    raise TimeoutError("UART capture timeout")

                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.extend(data)
                    _touch_recovery_from_uart()
                    last_recv_t = time.time()
                except socket.timeout:
                    # idle detection
                    if (time.time() - last_recv_t) >= uart_output_idle_sec:
                        break

            # 7) 恢复判定：ERROR_RECOVERED 走 USART1→TCP，不在 QEMU stdout；须在已收 UART 缓冲中检测。
            _touch_recovery_from_uart()

            # 7b) 堆栈水位线：QEMU 仍在跑、gdbstub 可用时 dump SRAM（先于 stop_qemu）
            stack_wm: Optional[Dict[str, Any]] = None
            elf_p = firmware_elf_path
            if elf_p is None:
                cand = firmware_bin_path.with_suffix(".elf")
                if cand.is_file():
                    elf_p = cand
            wm_merged = merge_stack_watermark_config(
                stack_watermark_cfg,
                fc,
            )
            if (
                wm_merged.get("enabled", True)
                and elf_p is not None
                and elf_p.is_file()
                and self.qemu_mgr._baremetal_gdb_port is not None
            ):
                gdb_to = float(fc.get("gdb_timeout_sec") or 15.0)
                try:
                    stack_wm = collect_stack_watermark(
                        elf_path=elf_p,
                        gdb_exe=gdb_command_for_subprocess(fc),
                        gdb_host=str(
                            getattr(self.qemu_mgr, "_baremetal_gdb_host", "127.0.0.1")
                        ),
                        gdb_port=int(self.qemu_mgr._baremetal_gdb_port),
                        gdb_timeout_sec=gdb_to + 5.0,
                        wm_cfg=wm_merged,
                        fault_cfg=fc,
                    )
                except Exception as e:  # pragma: no cover
                    stack_wm = {"enabled": True, "error": str(e)[:200]}
                    logger(f"栈水位线采集异常: {e}")

            if stack_wm is not None:
                stack_wm = enrich_stack_watermark_result(
                    stack_wm, stack_watermark_cfg, fc
                )
                summary = format_watermark_summary(stack_wm)
                if summary:
                    logger(summary)

            #    For injection mode, wait a bit for ERROR_RECOVERED (UART 或 logger/QEMU 行补充)。
            if recovery_event is not None:
                remaining = max(0.0, total_timeout_sec - (time.time() - start_t))
                recovery_success = recovery_event.wait(timeout=remaining)
            else:
                recovery_success = None

            out_text = chunks.decode("utf-8", errors="ignore")
            return BareMetalRunResult(
                actual_output=out_text,
                exec_time_ms=int((time.time() - start_t) * 1000),
                recovery_success=recovery_success,
                stack_watermark=stack_wm,
            )
        finally:
            try:
                self.qemu_mgr.stop_qemu()
            except Exception:
                pass

