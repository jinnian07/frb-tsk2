from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from judger.core.map_resource_usage import analyze_map_usage


_MAP_SAMPLE = """
Archive member included to satisfy reference by file (symbol)

Memory Configuration

Name             Origin             Length             Attributes
FLASH            0x08000000         0x00020000         xr
RAM              0x20000000         0x00002000         xrw
*default*        0x00000000         0xffffffff

Linker script and memory map

.isr_vector      0x08000000       0x00000100
.text            0x08000100       0x00000200
.rodata          0x08000300       0x00000080
.data            0x20000000       0x00000040 load address 0x08000380
.bss             0x20000040       0x00000020
._stack_top      0x20001f00       0x00000100
                0x20002000                _estack = (ORIGIN (RAM) + LENGTH (RAM))
                0x20000060                _ebss = .
"""


_LD_SAMPLE = """
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 128K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 8K
}
"""


class TestMapResourceUsage(unittest.TestCase):
    def test_parse_usage_from_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "firmware.map"
            ld_path = Path(tmp) / "linker.ld"
            map_path.write_text(_MAP_SAMPLE, encoding="utf-8")
            ld_path.write_text(_LD_SAMPLE, encoding="utf-8")

            report = analyze_map_usage(map_path, linker_script=ld_path)

            self.assertEqual(report["limits"]["source"], "linker_script")
            self.assertEqual(report["limits"]["flash_bytes"], 131072)
            self.assertEqual(report["limits"]["ram_bytes"], 8192)

            flash = report["sections_summary_bytes"]["flash"]
            ram = report["sections_summary_bytes"]["ram"]
            self.assertEqual(flash["code"], 0x300)
            self.assertEqual(flash["rodata"], 0x80)
            self.assertEqual(flash["data_lma"], 0x40)
            self.assertEqual(flash["total_used"], 0x3C0)

            self.assertEqual(ram["data"], 0x40)
            self.assertEqual(ram["bss"], 0x20)
            self.assertEqual(ram["total_used"], 0x160)
            self.assertEqual(ram["stack_inferred_bytes"], 0x1FA0)


if __name__ == "__main__":
    unittest.main()
