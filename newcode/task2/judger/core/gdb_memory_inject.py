"""
通过 arm-none-eabi-gdb + QEMU gdbstub 对目标 RAM 做 32 位字异或位翻转。
连接时目标会短暂停住；改完后 detach，QEMU 继续运行。
"""
from __future__ import annotations

import subprocess
from typing import Optional


def align_word_address(addr: int) -> int:
    return int(addr) & ~3


def gdb_memory_bitflip(
    gdb_executable: str,
    *,
    host: str,
    port: int,
    address: int,
    bit: int,
    timeout_sec: float = 15.0,
) -> tuple[bool, Optional[str]]:
    """
    Flip one bit in the 32-bit word containing `address` (word-aligned).
    Returns (ok, error_message).
    """
    word = align_word_address(address)
    b = int(bit) & 31
    host = host.strip() or "127.0.0.1"

    # 使用 GDB 临时变量避免 shell 转义问题
    cmd = [
        gdb_executable,
        "-batch-silent",
        "-ex",
        f"target extended-remote {host}:{int(port)}",
        "-ex",
        f"set $__oj_w = *(unsigned int*){word}",
        "-ex",
        f"set {{unsigned int}}{word} = $__oj_w ^ (1 << {b})",
        "-ex",
        "detach",
        "-ex",
        "quit",
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "gdb subprocess timeout"
    except FileNotFoundError:
        return False, f"gdb not found: {gdb_executable}"
    except OSError as e:
        return False, str(e)

    if r.returncode != 0:
        err = (r.stderr or "").strip() or (r.stdout or "").strip() or f"exit {r.returncode}"
        return False, err
    return True, None
