from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# GUI / JudgeService / API 摘要共用前缀
RESOURCE_USAGE_LOG_PREFIX = "资源占用（静态，.map）："


_TOP_SECTION_RE = re.compile(
    r"^\s*(\.[A-Za-z0-9_.$-]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)(?:\s+load address 0x([0-9a-fA-F]+))?"
)
_MEMCFG_ROW_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\b"
)
_MEMORY_REGION_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*([^,]+),\s*LENGTH\s*=\s*([^\s/]+)"
)
_HEX_SYMBOL_RE = re.compile(r"0x([0-9a-fA-F]+)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _parse_size_literal(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        suffix = text[-1].lower()
        if suffix in {"k", "m", "g"}:
            base = float(text[:-1])
            unit = {"k": 1024, "m": 1024**2, "g": 1024**3}[suffix]
            return int(base * unit)
        return int(text, 10)
    except Exception:
        return None


def _collect_memory_from_map(lines: list[str]) -> dict[str, dict[str, int]]:
    regions: dict[str, dict[str, int]] = {}
    in_memcfg = False
    for line in lines:
        if "Memory Configuration" in line:
            in_memcfg = True
            continue
        if in_memcfg and "Linker script and memory map" in line:
            break
        if not in_memcfg:
            continue
        m = _MEMCFG_ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1).upper()
        origin = int(m.group(2), 16)
        length = int(m.group(3), 16)
        regions[name] = {"origin": origin, "length": length}
    return regions


def _collect_memory_from_linker_script(linker_script: Path) -> dict[str, dict[str, int]]:
    text = linker_script.read_text(encoding="utf-8", errors="ignore")
    regions: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        m = _MEMORY_REGION_RE.match(line)
        if not m:
            continue
        name = m.group(1).upper()
        origin = _parse_size_literal(m.group(2))
        length = _parse_size_literal(m.group(3))
        if origin is None or length is None:
            continue
        regions[name] = {"origin": origin, "length": length}
    return regions


def _load_limits_json(path: Path) -> tuple[int | None, int | None]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    flash = obj.get("flash_bytes")
    ram = obj.get("ram_bytes")
    flash_i = int(flash) if flash is not None else None
    ram_i = int(ram) if ram is not None else None
    return flash_i, ram_i


def _locate_region_name(regions: dict[str, dict[str, int]], address: int) -> str | None:
    for name, info in regions.items():
        start = info["origin"]
        end = start + info["length"]
        if start <= address < end:
            return name
    return None


def _is_code_section(name: str) -> bool:
    return name.startswith(
        (
            ".text",
            ".isr_vector",
            ".init",
            ".fini",
            ".plt",
            ".glue",
            ".interwork",
        )
    )


def _is_rodata_section(name: str) -> bool:
    return name.startswith((".rodata", ".ARM.extab", ".ARM.exidx", ".eh_frame"))


def _is_data_section(name: str) -> bool:
    return name.startswith((".data", ".ramfunc"))


def _is_bss_section(name: str) -> bool:
    return name.startswith((".bss", ".sbss", ".tbss", ".COMMON"))


def analyze_map_usage(
    map_file: Path,
    *,
    linker_script: Path | None = None,
    limits_json: Path | None = None,
) -> dict[str, Any]:
    lines = map_file.read_text(encoding="utf-8", errors="ignore").splitlines()

    map_regions = _collect_memory_from_map(lines)
    linker_regions = _collect_memory_from_linker_script(linker_script) if linker_script else {}

    limits_source = "unknown"
    flash_limit: int | None = None
    ram_limit: int | None = None

    if limits_json is not None:
        flash_limit, ram_limit = _load_limits_json(limits_json)
        limits_source = "config"
    elif linker_regions:
        flash_limit = linker_regions.get("FLASH", {}).get("length")
        ram_limit = linker_regions.get("RAM", {}).get("length")
        limits_source = "linker_script"
    elif map_regions:
        flash_limit = map_regions.get("FLASH", {}).get("length")
        ram_limit = map_regions.get("RAM", {}).get("length")
        limits_source = "map_memory_configuration"

    code_bytes = 0
    rodata_bytes = 0
    data_bytes = 0
    bss_bytes = 0
    ram_noload_bytes = 0
    data_lma_flash_bytes = 0
    flash_other_bytes = 0
    ram_other_bytes = 0
    sections: list[dict[str, Any]] = []

    for line in lines:
        m = _TOP_SECTION_RE.match(line)
        if not m:
            continue
        sec_name = m.group(1)
        addr = int(m.group(2), 16)
        size = int(m.group(3), 16)
        load_addr = int(m.group(4), 16) if m.group(4) else None
        if size <= 0:
            continue

        region = _locate_region_name(map_regions or linker_regions, addr)
        if region is None:
            region = "UNKNOWN"

        sections.append(
            {
                "name": sec_name,
                "vma": addr,
                "size_bytes": size,
                "region": region,
                "lma": load_addr,
            }
        )

        is_noload = "noload" in line.lower()
        if region == "FLASH":
            if _is_code_section(sec_name):
                code_bytes += size
            elif _is_rodata_section(sec_name):
                rodata_bytes += size
            else:
                flash_other_bytes += size
        elif region == "RAM":
            if _is_data_section(sec_name):
                data_bytes += size
            elif _is_bss_section(sec_name):
                bss_bytes += size
            elif is_noload:
                ram_noload_bytes += size
            else:
                ram_other_bytes += size

            if load_addr is not None:
                lma_region = _locate_region_name(map_regions or linker_regions, load_addr)
                if lma_region == "FLASH":
                    data_lma_flash_bytes += size

    symbol_addrs: dict[str, int] = {}
    for line in lines:
        m = _HEX_SYMBOL_RE.search(line)
        if m:
            symbol_addrs[m.group(2)] = int(m.group(1), 16)

    stack_reserved = None
    stack_inferred = None
    if "_estack" in symbol_addrs and "_sstack" in symbol_addrs:
        stack_reserved = abs(symbol_addrs["_estack"] - symbol_addrs["_sstack"])
    elif "_estack" in symbol_addrs and "_ebss" in symbol_addrs:
        # In this project linker script, [_ebss, _estack) is available to stack/heap.
        stack_inferred = max(0, symbol_addrs["_estack"] - symbol_addrs["_ebss"])

    heap_reserved = None
    if "__heap_limit" in symbol_addrs and "__heap_base" in symbol_addrs:
        heap_reserved = max(0, symbol_addrs["__heap_limit"] - symbol_addrs["__heap_base"])
    elif "__heap_limit" in symbol_addrs and "_end" in symbol_addrs:
        heap_reserved = max(0, symbol_addrs["__heap_limit"] - symbol_addrs["_end"])

    flash_used = code_bytes + rodata_bytes + data_lma_flash_bytes + flash_other_bytes
    ram_used = data_bytes + bss_bytes + ram_noload_bytes + ram_other_bytes

    flash_percent = (flash_used / flash_limit * 100.0) if flash_limit else None
    ram_percent = (ram_used / ram_limit * 100.0) if ram_limit else None

    notes: list[str] = []
    notes.append("stack/heap values are static estimates from map symbols and linker layout only.")
    if stack_inferred is not None and stack_reserved is None:
        notes.append("stack_inferred_bytes is available free RAM above _ebss, not real runtime stack peak.")
    if heap_reserved is None:
        notes.append("heap reservation symbols not found; heap_reserved_bytes is unknown.")

    return {
        "map_file": str(map_file),
        "limits": {
            "source": limits_source,
            "flash_bytes": flash_limit,
            "ram_bytes": ram_limit,
        },
        "sections_summary_bytes": {
            "flash": {
                "code": code_bytes,
                "rodata": rodata_bytes,
                "data_lma": data_lma_flash_bytes,
                "other": flash_other_bytes,
                "total_used": flash_used,
            },
            "ram": {
                "data": data_bytes,
                "bss": bss_bytes,
                "noload": ram_noload_bytes,
                "other": ram_other_bytes,
                "total_used": ram_used,
                "stack_reserved_bytes": stack_reserved,
                "stack_inferred_bytes": stack_inferred,
                "heap_reserved_bytes": heap_reserved,
            },
        },
        "utilization_percent": {
            "flash": flash_percent,
            "ram": ram_percent,
        },
        "notes": notes,
        "sections": sections,
    }


def format_resource_usage_summary(report: dict[str, Any]) -> str:
    """与 CLI 首行输出一致，供 HTTP/API 等嵌入展示。"""
    return _human_summary(report)


def _human_summary(report: dict[str, Any]) -> str:
    flash_used = report["sections_summary_bytes"]["flash"]["total_used"]
    ram_used = report["sections_summary_bytes"]["ram"]["total_used"]
    flash_limit = report["limits"]["flash_bytes"]
    ram_limit = report["limits"]["ram_bytes"]
    flash_pct = report["utilization_percent"]["flash"]
    ram_pct = report["utilization_percent"]["ram"]
    flash_txt = (
        f"FLASH占用率 {flash_pct:.2f}% ({flash_used}/{flash_limit} bytes)"
        if flash_pct is not None
        else f"FLASH占用率 unknown ({flash_used}/unknown bytes)"
    )
    ram_txt = (
        f"RAM占用率 {ram_pct:.2f}% ({ram_used}/{ram_limit} bytes)"
        if ram_pct is not None
        else f"RAM占用率 unknown ({ram_used}/unknown bytes)"
    )
    return f"{flash_txt}; {ram_txt}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse GNU ld .map file and report Flash/RAM utilization."
    )
    parser.add_argument("--map", required=True, type=Path, help="Path to GNU ld .map file.")
    parser.add_argument(
        "--linker-script",
        type=Path,
        default=None,
        help="Optional linker script to parse MEMORY limits.",
    )
    parser.add_argument(
        "--limits-json",
        type=Path,
        default=None,
        help="Optional JSON with flash_bytes and ram_bytes.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional output file for JSON report.",
    )
    parser.add_argument(
        "--no-sections",
        action="store_true",
        help="Do not include detailed sections list in JSON output.",
    )
    args = parser.parse_args()

    report = analyze_map_usage(
        args.map,
        linker_script=args.linker_script,
        limits_json=args.limits_json,
    )
    if args.no_sections:
        report = dict(report)
        report["sections"] = []

    print(_human_summary(report))
    json_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json_text, encoding="utf-8")
        print(f"JSON written to {args.json_out}")
    else:
        print(json_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
