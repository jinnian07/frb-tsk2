"""
嵌入式 C（Cortex-M / freestanding）静态分析辅助：为 clang-tidy 构造与 baremetal 构建一致的编译参数。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def _arm_gcc_exe() -> Optional[Path]:
    for name in ("arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"):
        exe = shutil.which(name)
        if exe:
            return Path(exe).resolve()
    return None


def resolve_arm_none_eabi_include_dirs() -> List[Path]:
    """
    返回 arm-none-eabi 下用于 freestanding 解析的 -I 目录列表：
    - GCC 内置固定宽度类型等（-print-file-name=include）
    - newlib 风格 stdio.h 等：`<prefix>/arm-none-eabi/include`
    """
    out: List[Path] = []
    exe = _arm_gcc_exe()
    if exe is None:
        return out

    try:
        proc = subprocess.run(
            [str(exe), "-print-file-name=include"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None

    if proc and proc.returncode == 0:
        line = (proc.stdout or "").strip().splitlines()
        raw = line[0] if line else ""
        if raw and raw != "include":
            p = Path(raw).resolve()
            if p.is_dir():
                out.append(p)

    # .../bin -> 工具链根目录，其下 arm-none-eabi/include 为 newlib 头
    prefix = exe.parent.parent
    newlib_inc = prefix / "arm-none-eabi" / "include"
    if newlib_inc.is_dir() and newlib_inc not in out:
        out.append(newlib_inc.resolve())

    return out


def resolve_clang_resource_include() -> Optional[Path]:
    """
    降级：使用 clang 资源目录下的内置头（不完全等价 newlib，可无 arm-none-eabi-gcc 时兜底）。
    """
    for name in ("clang", "clang.exe"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            proc = subprocess.run(
                [exe, "-print-resource-dir"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        rd = (proc.stdout or "").strip().splitlines()
        if not rd:
            continue
        inc = Path(rd[0].strip()) / "include"
        if inc.is_dir():
            return inc.resolve()
    return None


def build_embedded_clang_tidy_compile_args(
    *,
    baremetal_dir: Path,
    extra_includes: Sequence[Path] = (),
) -> Tuple[List[str], List[str]]:
    """
    返回 (compile_args, notes)：notes 为人类可读说明（日志用）。
    """
    args: List[str] = [
        "-std=c11",
        "-target",
        "arm-none-eabi",
        "-ffreestanding",
        "-fno-builtin",
        "-mcpu=cortex-m3",
        "-mthumb",
        "-nostdlib",
        f"-I{baremetal_dir.resolve()}",
    ]
    notes: List[str] = []

    arm_incs = resolve_arm_none_eabi_include_dirs()
    if arm_incs:
        for inc in arm_incs:
            args.append(f"-I{inc}")
        notes.append("已加入 arm-none-eabi-gcc 头路径: " + "; ".join(str(p) for p in arm_incs))
    else:
        fallback = resolve_clang_resource_include()
        if fallback is not None:
            args.append(f"-I{fallback}")
            notes.append(
                f"未找到 arm-none-eabi-gcc，已降级使用 clang 内置头: {fallback}"
            )
        else:
            notes.append(
                "警告: 未找到 arm-none-eabi-gcc 与 clang 资源目录，"
                "stdint.h 等可能无法解析；请安装 Arm GNU Toolchain。"
            )

    for d in extra_includes:
        if d.is_dir():
            args.append(f"-I{d.resolve()}")

    return args, notes


def build_clang_tidy_command(
    *,
    source_file: Path,
    task2_dir: Path,
    extra_includes: Sequence[Path] = (),
) -> Tuple[List[str], List[str]]:
    """
    构造 clang-tidy 命令行；cwd 应为 task2_dir。
    临时源文件通常不在 compile_commands.json 中，故不自动加 -p，避免与 -- 后的显式参数冲突；
    对工程内固定文件可手动: clang-tidy -p task2 P0002/std.c
    """
    config = task2_dir / ".clang-tidy"
    cmd: List[str] = ["clang-tidy", str(source_file.resolve())]
    if config.is_file():
        cmd.extend(["--config-file", str(config.resolve())])

    comp_args, notes = build_embedded_clang_tidy_compile_args(
        baremetal_dir=task2_dir / "baremetal",
        extra_includes=extra_includes,
    )
    cmd.append("--")
    cmd.extend(comp_args)
    compile_db = task2_dir / "compile_commands.json"
    if compile_db.is_file():
        notes.append(
            "提示: 目录下存在 compile_commands.json，可用 "
            "`clang-tidy -p <task2> <源文件>` 按数据库标志检查已收录的翻译单元。"
        )
    return cmd, notes
