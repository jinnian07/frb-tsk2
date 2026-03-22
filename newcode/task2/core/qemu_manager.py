import subprocess
import threading
import time
import win32gui
import win32con
import os
from typing import Callable, Optional
from pathlib import Path

from core.fault_injection_config import gdb_command_for_subprocess
from core.gdb_memory_inject import gdb_memory_bitflip


def _pick_free_tcp_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


class QemuManager:
    def __init__(
        self,
        config,
        container_id: Optional[int] = None,
        *,
        fault_config: Optional[dict] = None,
    ):
        self.config = config
        self.container_id = container_id
        self._fault_config = fault_config or {}
        self.process = None
        self.hwnd = None
        self._log_callback: Optional[Callable[[str], None]] = None
        self._callback_lock = threading.Lock()
        self._output_thread: Optional[threading.Thread] = None
        self._stop_output = threading.Event()
        self._baremetal_gdb_host: str = "127.0.0.1"
        self._baremetal_gdb_port: Optional[int] = None

    def set_log_callback(self, log_callback: Optional[Callable[[str], None]]):
        # 可在单次判题期间动态替换回调，便于在 API 场景下按请求收集日志
        with self._callback_lock:
            self._log_callback = log_callback

    def _log(self, message: str):
        with self._callback_lock:
            cb = self._log_callback
        if cb:
            cb(message)

    def start_qemu(self, log_callback):
        # 启动或复用 QEMU：无论已有进程是否存在，都更新当前日志回调
        self.set_log_callback(log_callback)
        if self.process and self.process.poll() is None:
            self._log("QEMU 已在运行中。")
            return

        executable = self.config.get("executable", "qemu-system-aarch64")
        bios_path = self.config.get("bios", "")
        drive_path = self.config.get("drive", "")

        if bios_path and not os.path.exists(bios_path):
            log_callback(f"错误: BIOS文件不存在: {bios_path}")
            return

        if drive_path and not os.path.exists(drive_path):
            log_callback(f"错误: 镜像文件不存在: {drive_path}")
            return

        cmd = [
            executable,
            "-M", "virt", "-cpu", "cortex-a57", "-smp", "4", "-m", "4096M",
            "-name", "qemu-c,process=qemu-c-instance",
        ]

        if bios_path:
            cmd.extend(["-bios", bios_path])

        if drive_path:
            cmd.extend([
                "-drive", f"if=none,file={drive_path},id=hd0",
                "-device", "virtio-blk-pci,drive=hd0",
            ])

        cmd.extend([
            "-device", "virtio-net-pci,netdev=net0",
            "-netdev", "user,id=net0,hostfwd=tcp::2222-:22",
            "-device", "virtio-gpu-pci,xres=800,yres=600",
            "-device", "qemu-xhci", "-device", "usb-kbd", "-device", "usb-tablet",
            "-display", "sdl"
        ])

        try:
            self._log(f"正在启动 QEMU: {executable}")
            self._log(f"BIOS: {bios_path}")
            self._log(f"镜像: {drive_path}")
            self._stop_output.clear()
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            self._output_thread = threading.Thread(target=self._read_output_loop, daemon=True)
            self._output_thread.start()

            # API 场景 container_id=None 时不需要嵌入窗口，避免无谓的枚举超时。
            if self.container_id is not None:
                threading.Thread(target=self._embed_logic, args=(log_callback,), daemon=True).start()
        except FileNotFoundError:
            self._log(f"错误: 找不到 QEMU 可执行文件: {executable}")
        except Exception as e:
            self._log(f"启动失败: {e}")

    def start_qemu_baremetal(
        self,
        log_callback: Callable[[str], None],
        firmware_bin_path: Path,
        *,
        uart_host: str = "127.0.0.1",
        uart_port: int,
        machine: str = "stm32vldiscovery",
    ):
        """
        One-shot bare-metal QEMU session:
          - Cortex-M3 STM32VLDISCOVERY
          - USART1 UART1 is connected to a TCP socket chardev (server mode)
          - gdbstub on TCP（端口由 fault_injection_config 固定或自动选择）供 GDB 位翻转
        """
        self.set_log_callback(log_callback)

        # Stop previous session (bare-metal mode is intentionally one-shot per test point).
        if self.process and self.process.poll() is None:
            try:
                self.stop_qemu()
            except Exception:
                pass

        fc = self._fault_config
        gdb_port_cfg = int(fc.get("gdb_port") or 0)
        if gdb_port_cfg <= 0:
            gdb_port = _pick_free_tcp_port()
        else:
            gdb_port = gdb_port_cfg

        self._baremetal_gdb_host = str(fc.get("gdb_host") or "127.0.0.1").strip() or "127.0.0.1"
        self._baremetal_gdb_port = gdb_port

        executable = self.config.get("baremetal_executable", "qemu-system-arm")

        cmd = [
            executable,
            "-M",
            machine,
            "-kernel",
            str(firmware_bin_path),
            "-nographic",
            "-monitor",
            "none",
            "-gdb",
            f"tcp::{gdb_port}",
            "-chardev",
            f"socket,id=uart1,host={uart_host},port={uart_port},server,nowait",
            "-serial",
            "chardev:uart1",
            "-serial",
            "null",
            "-serial",
            "null",
        ]

        self._stop_output.clear()
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        self._output_thread = threading.Thread(target=self._read_output_loop, daemon=True)
        self._output_thread.start()

    def _read_output_loop(self):
        # 持续读取 QEMU 输出，给调用方的回调做“实时日志”输入
        try:
            if not self.process or not self.process.stdout:
                return
            for line in self.process.stdout:
                if self._stop_output.is_set():
                    return
                line = line.rstrip("\r\n")
                if line:
                    self._log(line)
        except Exception as e:
            self._log(f"读取 QEMU 输出失败: {e}")

    def apply_gdb_memory_bitflip(self, address: int, bit: int) -> None:
        """
        通过 arm-none-eabi-gdb 连接当前裸机会话的 gdbstub，对 SRAM 字做异或位翻转。
        """
        if self._baremetal_gdb_port is None:
            raise RuntimeError("当前未处于裸机 QEMU 会话，无法 GDB 注入")
        gdb_exe = gdb_command_for_subprocess(self._fault_config)
        timeout = float(self._fault_config.get("gdb_timeout_sec") or 15.0)
        ok, err = gdb_memory_bitflip(
            gdb_exe,
            host=self._baremetal_gdb_host,
            port=self._baremetal_gdb_port,
            address=address,
            bit=bit,
            timeout_sec=timeout,
        )
        if not ok:
            raise RuntimeError(f"GDB 位翻转失败: {err}")

    def _embed_logic(self, log_callback):
        target_title = "qemu-c"
        for attempt in range(60):
            if self.process.poll() is not None:
                log_callback("QEMU 进程已退出。")
                return

            found_hwnd = None
            def cb(hwnd, _):
                nonlocal found_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if target_title in title.lower():
                        found_hwnd = hwnd

            try:
                win32gui.EnumWindows(cb, None)
            except Exception as e:
                log_callback(f"枚举窗口错误: {e}")

            if found_hwnd:
                self.hwnd = found_hwnd
                actual_title = win32gui.GetWindowText(self.hwnd)
                log_callback(f"匹配到窗口: '{actual_title}' (HWND: {self.hwnd})")
                try:
                    if self.container_id:
                        win32gui.SetParent(self.hwnd, self.container_id)
                        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_STYLE)
                        new_style = style & ~win32con.WS_CAPTION & ~win32con.WS_THICKFRAME
                        win32gui.SetWindowLong(self.hwnd, win32con.GWL_STYLE, new_style)
                        win32gui.MoveWindow(self.hwnd, 0, 0, 800, 600, True)
                        log_callback("QEMU 窗口已就绪并嵌入。")
                    else:
                        log_callback("QEMU 窗口已就绪（未嵌入，因为 container_id 未提供）。")
                except Exception as e:
                    log_callback(f"窗口嵌入失败: {e}")
                return
            time.sleep(0.5)
        log_callback("窗口捕获超时，请检查 QEMU 状态。")

    def stop_qemu(self):
        if self.process:
            self._stop_output.set()
            self.process.terminate()
            self.process = None
        self._baremetal_gdb_port = None
