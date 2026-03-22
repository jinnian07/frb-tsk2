"""
独立配置：GDB 位翻转故障注入（不修改 config.json 语义）。
缺省文件时使用 DEFAULT_FAULT_INJECTION_CONFIG。

sram_flip_*：随机翻转地址落在 [low, high_exclusive) 内；教师可缩小窗口以对准链接脚本中的演示区。
inject_after_uart_input：true（默认）表示在发送 UART 测试输入之后再翻转，减轻启动即破坏的概率。
"""
from __future__ import annotations

import json
import os
import random
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

# STM32F100（stm32vldiscovery）片上 SRAM 8KB
_DEFAULT_SRAM_LOW = 0x20000000
_DEFAULT_SRAM_HIGH_EXCL = 0x20002000

DEFAULT_FAULT_INJECTION_CONFIG: Dict[str, Any] = {
    # 在 PATH 中查找；若 gdb_path 非空则优先使用该可执行文件绝对路径
    "gdb_executable": "arm-none-eabi-gdb",
    "gdb_path": "",
    "gdb_host": "127.0.0.1",
    # 0 或未设置：每次裸机会话自动选本地空闲端口
    "gdb_port": 0,
    "gdb_timeout_sec": 15,
    "inject_after_uart_input": True,
    "sram_flip_address_low": _DEFAULT_SRAM_LOW,
    "sram_flip_address_high_exclusive": _DEFAULT_SRAM_HIGH_EXCL,
}


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s, 10)
    return int(value)


def inject_after_uart_input(cfg: Dict[str, Any]) -> bool:
    """默认 True：在 sendall 之后再 GDB 翻转。"""
    v = cfg.get("inject_after_uart_input", True)
    if v is None:
        return True
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(v, (int, float)):
        return v != 0
    return True


def random_sram_flip_address(cfg: Dict[str, Any]) -> int:
    """
    在配置的 SRAM 窗口内随机选一字节地址（调用方再对齐到字或交给 GDB 字翻转逻辑）。
    返回 [low, high_exclusive-1]。
    """
    low = _coerce_int(
        cfg.get("sram_flip_address_low"), _DEFAULT_SRAM_LOW
    )
    high_excl = _coerce_int(
        cfg.get("sram_flip_address_high_exclusive"), _DEFAULT_SRAM_HIGH_EXCL
    )
    if high_excl <= low + 1:
        high_excl = low + 4
    return random.randint(low, high_excl - 1)


def load_fault_injection_config(
    task2_root: Path | None = None,
    filename: str = "fault_injection_config.json",
) -> Dict[str, Any]:
    cfg = deepcopy(DEFAULT_FAULT_INJECTION_CONFIG)
    root = task2_root or Path(__file__).resolve().parent.parent
    path = root / filename
    if not path.is_file():
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update(user)
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def gdb_command_for_subprocess(cfg: Dict[str, Any]) -> str:
    p = (cfg.get("gdb_path") or "").strip()
    if p and os.path.isfile(p):
        return p
    return (cfg.get("gdb_executable") or "arm-none-eabi-gdb").strip() or "arm-none-eabi-gdb"
