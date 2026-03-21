import subprocess
import threading
import time
import win32gui
import win32con
import os
import random
from typing import Callable, Optional
from pathlib import Path

class QemuManager:
    def __init__(self, config, container_id: Optional[int] = None):
        self.config = config
        self.container_id = container_id
        self.process = None
        self.hwnd = None
        self._log_callback: Optional[Callable[[str], None]] = None
        self._callback_lock = threading.Lock()
        self._output_thread: Optional[threading.Thread] = None
        self._stop_output = threading.Event()

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
        
        executable = self.config.get('executable', 'qemu-system-aarch64')
        bios_path = self.config.get('bios', '')
        drive_path = self.config.get('drive', '')
        
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
            # stdin=PIPE 用于故障注入/monitor 命令下发；stdout+stderr 合并便于恢复检测
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
          - QEMU monitor uses stdio so `send_debug_command()` can inject faults
        """
        self.set_log_callback(log_callback)

        # Stop previous session (bare-metal mode is intentionally one-shot per test point).
        if self.process and self.process.poll() is None:
            try:
                self.stop_qemu()
            except Exception:
                pass

        executable = self.config.get("baremetal_executable", "qemu-system-arm")

        cmd = [
            executable,
            "-M",
            machine,
            "-kernel",
            str(firmware_bin_path),
            "-nographic",
            "-monitor",
            "stdio",
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

    def send_debug_command(self, command: str):
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("QEMU 未运行，无法注入故障")
        if not self.process.stdin:
            raise RuntimeError("QEMU 进程 stdin 未初始化，无法发送 debug 命令")
        self.process.stdin.write(f"{command}\n")
        self.process.stdin.flush()

    def inject_fault(self, fault_type: str = "memory_bitflip"):
        """
        注入故障（与原 GUI 逻辑一致：memory_bitflip 随机选择地址+bit 并下发 inject_error）。
        """
        if fault_type == "memory_bitflip":
            address = random.randint(0x20000000, 0x20010000)
            bit = random.randint(0, 31)
            self.send_debug_command(f"inject_error {address} {bit}")

    def inject_fault_baremetal_memory_bitflip(
        self,
        address_low: int = 0x20000000,
        address_high_exclusive: int = 0x20002000,
    ):
        """
        Bare-metal STM32F100 SRAM range:
          - SRAM_BASE_ADDRESS = 0x20000000
          - SRAM_SIZE = 8K => end exclusive 0x20002000
        """
        address = random.randint(address_low, address_high_exclusive - 1)
        bit = random.randint(0, 31)
        self.send_debug_command(f"inject_error {address} {bit}")

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
