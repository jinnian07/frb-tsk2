import subprocess
import threading
import time
import win32gui
import win32con
import os

class QemuManager:
    def __init__(self, config, container_id):
        self.config = config
        self.container_id = container_id
        self.process = None
        self.hwnd = None

    def start_qemu(self, log_callback):
        if self.process and self.process.poll() is None:
            log_callback("QEMU 已在运行中。")
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
            log_callback(f"正在启动 QEMU: {executable}")
            log_callback(f"BIOS: {bios_path}")
            log_callback(f"镜像: {drive_path}")
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            threading.Thread(target=self._embed_logic, args=(log_callback,), daemon=True).start()
        except FileNotFoundError:
            log_callback(f"错误: 找不到 QEMU 可执行文件: {executable}")
        except Exception as e:
            log_callback(f"启动失败: {e}")

    def _embed_logic(self, log_callback):
        target_title = "qemu-c"
        for attempt in range(60):
            if self.process.poll() is not None:
                stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore') if self.process.stderr else ""
                log_callback(f"QEMU 进程已退出，错误: {stderr_output[:200]}")
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
                    win32gui.SetParent(self.hwnd, self.container_id)
                    style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_STYLE)
                    new_style = style & ~win32con.WS_CAPTION & ~win32con.WS_THICKFRAME
                    win32gui.SetWindowLong(self.hwnd, win32con.GWL_STYLE, new_style)
                    win32gui.MoveWindow(self.hwnd, 0, 0, 800, 600, True)
                    log_callback("QEMU 窗口已就绪并嵌入。")
                except Exception as e:
                    log_callback(f"窗口嵌入失败: {e}")
                return
            time.sleep(0.5)
        log_callback("窗口捕获超时，请检查 QEMU 状态。")

    def stop_qemu(self):
        if self.process:
            self.process.terminate()
            self.process = None
