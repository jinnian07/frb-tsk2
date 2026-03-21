from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BareMetalArtifacts:
    elf_path: Path
    bin_path: Path


class BareMetalBuilder:
    """
    Build STM32 bare-metal firmware for QEMU `stm32vldiscovery` (Cortex-M3).

    The user-provided code is expected to be a complete C program with `main()`,
    potentially using stdio (scanf/printf). We link it with:
      - startup + vector table
      - UART1 driver
      - newlib syscalls: _read/_write mapped to UART
    """

    def __init__(
        self,
        toolchain_prefix: str = "arm-none-eabi",
        runtime_dir: Optional[Path] = None,
    ):
        task2_root = Path(__file__).resolve().parents[1]
        self.runtime_dir = runtime_dir or (task2_root / "baremetal")

        self.gcc = f"{toolchain_prefix}-gcc"
        self.ld = f"{toolchain_prefix}-ld"
        self.objcopy = f"{toolchain_prefix}-objcopy"

        self.linker_script = self.runtime_dir / "linker_stm32vldiscovery.ld"
        self.startup_c = self.runtime_dir / "startup_stm32vldiscovery.c"
        self.uart_c = self.runtime_dir / "uart1_qemu.c"
        self.syscalls_c = self.runtime_dir / "syscalls_newlib.c"

    def build(
        self,
        main_c_path: Path,
        out_dir: Path,
        *,
        extra_cflags: Optional[list[str]] = None,
    ) -> BareMetalArtifacts:
        out_dir.mkdir(parents=True, exist_ok=True)

        elf_path = out_dir / "firmware.elf"
        bin_path = out_dir / "firmware.bin"

        # Note:
        # - QEMU's stm32vldiscovery sets SYSCLK to 24MHz, Cortex-M3 core.
        # - We rely on linker script for memory layout.
        # - We link in startup + syscalls + UART driver.
        extra_cflags = extra_cflags or []

        cflags = [
            "-mcpu=cortex-m3",
            "-mthumb",
            "-O2",
            "-g",
            "-ffreestanding",
            "-fno-builtin",
            "-fdata-sections",
            "-ffunction-sections",
            "-fno-exceptions",
            "-fno-asynchronous-unwind-tables",
            "-nostartfiles",
        ]

        ldflags = [
            "-T",
            str(self.linker_script),
            "-Wl,--entry=Reset_Handler",
            "-Wl,--gc-sections",
            # newlib-nano is usually preferred; nosys specs provides stubs besides ours
            "-specs=nosys.specs",
        ]

        cmd = [
            self.gcc,
            *cflags,
            str(main_c_path),
            str(self.startup_c),
            str(self.uart_c),
            str(self.syscalls_c),
            *extra_cflags,
            *ldflags,
            "-o",
            str(elf_path),
        ]

        env = os.environ.copy()
        subprocess.run(cmd, check=True, cwd=str(out_dir), env=env)

        # QEMU -kernel for STM32 machines commonly uses a raw binary image.
        objcopy_cmd = [
            self.objcopy,
            "-O",
            "binary",
            "-S",
            "--gap-fill=0xFF",
            str(elf_path),
            str(bin_path),
        ]
        subprocess.run(objcopy_cmd, check=True, cwd=str(out_dir), env=env)

        return BareMetalArtifacts(elf_path=elf_path, bin_path=bin_path)

