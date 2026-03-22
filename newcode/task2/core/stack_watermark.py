"""
堆栈水位线：固件在启动时将 [_ebss,_estack) 染成 0xDEADBEEF；UART 采集结束后、终止 QEMU 前，
通过标准 arm-none-eabi-gdb + QEMU gdbstub interrupt + dump binary memory 读 SRAM 并扫描。

不依赖 QEMU monitor 自定义命令；与 fault_injection_config 共用 gdb 可执行文件与 SRAM 范围约定。

顺序：UART 输出空闲 → GDB dump（仍在运行）→ recovery_event 等待 → finally 里 stop_qemu。

GDB ``dump binary memory`` 的结束地址为**排他**上界（与 ``[lo, hi)`` 一致），故使用
``end_exclusive = ram_start + ram_size``。
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from core.fault_injection_config import gdb_command_for_subprocess

# 与 baremetal/startup_stm32vldiscovery.c 中 OJ_STACK_PAINT_U32 一致
OJ_STACK_PAINT_U32 = 0xDEADBEEF

RiskLevel = Literal["low", "medium", "high"]


def default_stack_watermark_config(fault_cfg: Optional[dict] = None) -> Dict[str, Any]:
    fc = fault_cfg or {}
    low = int(fc.get("sram_flip_address_low") or 0x20000000)
    high_excl = int(fc.get("sram_flip_address_high_exclusive") or 0x20002000)
    size = max(0, high_excl - low)
    return {
        "enabled": True,
        "safe_margin_bytes": 64,
        "warn_usage_ratio": 0.80,
        "high_usage_ratio": 0.95,
        "ram_start": low,
        "ram_size": size if size > 0 else 8192,
        # 子项量化（0～100）：D=depth_bytes，S=avail_bytes；危险段为方案 A
        "score_enabled": True,
        "score_safe_ratio": 0.7,
        "score_warn_ratio": 0.9,
        "score_warn_coefficient": 50.0,
        "score_danger_linear": 100.0,
        "score_danger_fixed": 20.0,
    }


def merge_stack_watermark_config(
    top_level: Optional[dict],
    fault_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    cfg = default_stack_watermark_config(fault_cfg)
    if isinstance(top_level, dict):
        cfg.update({k: v for k, v in top_level.items() if v is not None})
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["safe_margin_bytes"] = max(0, int(cfg.get("safe_margin_bytes") or 0))
    cfg["warn_usage_ratio"] = float(cfg.get("warn_usage_ratio") or 0.80)
    cfg["high_usage_ratio"] = float(cfg.get("high_usage_ratio") or 0.95)
    cfg["ram_start"] = int(cfg.get("ram_start") or cfg["ram_start"])
    cfg["ram_size"] = max(1, int(cfg.get("ram_size") or cfg["ram_size"]))
    cfg["score_enabled"] = bool(cfg.get("score_enabled", True))
    cfg["score_safe_ratio"] = float(cfg.get("score_safe_ratio") or 0.7)
    cfg["score_warn_ratio"] = float(cfg.get("score_warn_ratio") or 0.9)
    cfg["score_warn_coefficient"] = float(cfg.get("score_warn_coefficient") or 50.0)
    cfg["score_danger_linear"] = float(cfg.get("score_danger_linear") or 100.0)
    cfg["score_danger_fixed"] = float(cfg.get("score_danger_fixed") or 20.0)
    return cfg


def _clamp_score_round(x: float) -> int:
    return int(max(0, min(100, round(x))))


def score_stack_watermark(
    D: Optional[int],
    S: Optional[int],
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    D=depth_bytes，S=avail_bytes。缺失或 S<=0 返回 None（与 D>S 得 0 区分）。
    """
    if D is None or S is None:
        return None
    Si = int(S)
    if Si <= 0:
        return None
    Di = float(int(D))
    Sf = float(Si)
    c = cfg or {}
    r1 = float(c.get("score_safe_ratio", 0.7))
    r2 = float(c.get("score_warn_ratio", 0.9))
    c_warn = float(c.get("score_warn_coefficient", 50.0))
    c_d_lin = float(c.get("score_danger_linear", 100.0))
    c_d_fix = float(c.get("score_danger_fixed", 20.0))

    t1 = r1 * Sf
    t2 = r2 * Sf

    if Di <= t1:
        return 100
    if Di <= t2:
        raw = 100.0 - (Di - t1) / Sf * c_warn
        return _clamp_score_round(raw)
    if Di <= Sf:
        score_at_09 = 100.0 - (t2 - t1) / Sf * c_warn
        raw = score_at_09 - ((Di - t2) / Sf * c_d_lin + c_d_fix)
        return _clamp_score_round(raw)
    return 0


def stack_watermark_tier(
    D: Optional[int],
    S: Optional[int],
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if D is None or S is None or int(S) <= 0:
        return None
    Di = float(int(D))
    Sf = float(int(S))
    c = cfg or {}
    r1 = float(c.get("score_safe_ratio", 0.7))
    r2 = float(c.get("score_warn_ratio", 0.9))
    if Di <= r1 * Sf:
        return "安全"
    if Di <= r2 * Sf:
        return "预警"
    if Di <= Sf:
        return "危险"
    return "严重"


def enrich_stack_watermark_result(
    sw: Optional[Dict[str, Any]],
    wm_cfg: Optional[dict],
    fault_cfg: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """写入 stack_watermark_score / stack_watermark_tier；不改变 AC/WA 用的原始深度字段。"""
    if not sw:
        return None
    merged = merge_stack_watermark_config(wm_cfg, fault_cfg)
    out = dict(sw)
    if not merged.get("score_enabled", True):
        out["stack_watermark_score"] = None
        out["stack_watermark_tier"] = None
        return out
    D = out.get("depth_bytes")
    S = out.get("avail_bytes")
    Di = int(D) if D is not None else None
    Si = int(S) if S is not None else None
    out["stack_watermark_score"] = score_stack_watermark(Di, Si, merged)
    out["stack_watermark_tier"] = stack_watermark_tier(Di, Si, merged)
    return out


def _run_capture(cmd: list[str], timeout: float) -> tuple[str, str, int]:
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return r.stdout or "", r.stderr or "", r.returncode


def parse_elf_ebss_estack(
    elf_path: Path, *, nm_executable: str = "arm-none-eabi-nm"
) -> Tuple[Optional[int], Optional[int]]:
    """
    从 ELF 解析 _ebss、_estack 的数值地址（nm 首列为十六进制地址）。
    """
    try:
        r = subprocess.run(
            [nm_executable, str(elf_path.resolve())],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None
    if r.returncode != 0:
        return None, None
    ebss: Optional[int] = None
    estack: Optional[int] = None
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sym = parts[-1]
        if sym not in ("_ebss", "_estack"):
            continue
        try:
            val = int(parts[0], 16)
        except ValueError:
            continue
        if sym == "_ebss":
            ebss = val
        else:
            estack = val
    return ebss, estack


def gdb_dump_ram(
    gdb_exe: str,
    *,
    host: str,
    port: int,
    ram_start: int,
    ram_size: int,
    out_path: Path,
    timeout_sec: float = 20.0,
) -> tuple[bool, Optional[str]]:
    """
    连接 gdbstub、interrupt、将 [ram_start, ram_start+ram_size) 导出到二进制文件。
    """
    host = (host or "127.0.0.1").strip() or "127.0.0.1"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # GNU GDB: dump binary memory file lo hi — hi is the first address *not* dumped (exclusive).
    end_exclusive = ram_start + ram_size
    cmd = [
        gdb_exe,
        "-batch-silent",
        "-ex",
        f"set pagination off",
        "-ex",
        f"target extended-remote {host}:{int(port)}",
        "-ex",
        "interrupt",
        "-ex",
        f"dump binary memory {out_path.resolve()} {ram_start:#x} {end_exclusive:#x}",
        "-ex",
        "detach",
        "-ex",
        "quit",
    ]
    try:
        out, err, code = _run_capture(cmd, timeout_sec)
    except FileNotFoundError:
        return False, f"gdb not found: {gdb_exe}"
    except subprocess.TimeoutExpired:
        return False, "gdb dump timeout"
    except OSError as e:
        return False, str(e)
    if code != 0:
        msg = (err or "").strip() or (out or "").strip() or f"gdb exit {code}"
        return False, msg
    if not out_path.is_file() or out_path.stat().st_size < ram_size // 2:
        return False, "dump file missing or too small"
    return True, None


def compute_watermark_from_ram_dump(
    dump: bytes,
    *,
    ram_start: int,
    ebss: int,
    estack: int,
    paint: int = OJ_STACK_PAINT_U32,
    safe_margin_bytes: int = 64,
    warn_usage_ratio: float = 0.80,
    high_usage_ratio: float = 0.95,
) -> Dict[str, Any]:
    lo = (ebss + 3) & ~3
    hi = estack
    avail = max(0, hi - lo)
    if avail <= 0:
        return {
            "error": "invalid stack bounds from ELF",
            "avail_bytes": 0,
            "depth_bytes": None,
            "min_sp_estimate": None,
            "risk": "high",
        }

    base = ram_start
    if lo < base or hi > base + len(dump):
        return {
            "error": "ELF stack range outside dumped RAM window",
            "avail_bytes": avail,
            "depth_bytes": None,
            "min_sp_estimate": None,
            "risk": "high",
        }

    first_non = None
    off_lo = lo - base
    off_hi_excl = hi - base
    for off in range(off_lo, off_hi_excl, 4):
        if off + 4 > len(dump):
            break
        (w,) = struct.unpack_from("<I", dump, off)
        if w != (paint & 0xFFFFFFFF):
            first_non = base + off
            break

    if first_non is None:
        depth = 0
        min_sp_est = hi
        headroom = avail
    else:
        depth = hi - first_non
        min_sp_est = first_non
        headroom = first_non - lo

    usage_ratio = (depth / avail) if avail else 1.0
    risk: RiskLevel = "low"
    if headroom < safe_margin_bytes or usage_ratio >= high_usage_ratio:
        risk = "high"
    elif usage_ratio >= warn_usage_ratio:
        risk = "medium"

    return {
        "avail_bytes": avail,
        "depth_bytes": depth,
        "min_sp_estimate": min_sp_est,
        "headroom_bytes": headroom,
        "usage_ratio": round(usage_ratio, 4),
        "risk": risk,
        "stack_lo": lo,
        "stack_hi": hi,
    }


def collect_stack_watermark(
    *,
    elf_path: Path,
    gdb_exe: str,
    gdb_host: str,
    gdb_port: int,
    gdb_timeout_sec: float,
    wm_cfg: Dict[str, Any],
    fault_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    if not wm_cfg.get("enabled", True):
        return {"enabled": False}

    nm_exe = toolchain_nm_from_gdb(gdb_exe)
    ebss, estack = parse_elf_ebss_estack(elf_path, nm_executable=nm_exe)
    if ebss is None or estack is None:
        return {"error": "could not parse _ebss/_estack from ELF", "enabled": True}

    ram_start = int(wm_cfg.get("ram_start") or 0x20000000)
    ram_size = int(wm_cfg.get("ram_size") or 8192)
    merged = merge_stack_watermark_config({"ram_start": ram_start, "ram_size": ram_size}, fault_cfg)
    ram_start = merged["ram_start"]
    ram_size = merged["ram_size"]

    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
        dump_path = Path(tf.name)
    try:
        ok, err = gdb_dump_ram(
            gdb_exe,
            host=gdb_host,
            port=gdb_port,
            ram_start=ram_start,
            ram_size=ram_size,
            out_path=dump_path,
            timeout_sec=gdb_timeout_sec,
        )
        if not ok:
            return {
                "enabled": True,
                "error": err or "gdb dump failed",
                "avail_bytes": max(0, estack - ((ebss + 3) & ~3)),
                "depth_bytes": None,
                "min_sp_estimate": None,
                "risk": "high",
            }
        data = dump_path.read_bytes()
    finally:
        try:
            if dump_path.is_file():
                dump_path.unlink()
        except OSError:
            pass

    stats = compute_watermark_from_ram_dump(
        data,
        ram_start=ram_start,
        ebss=ebss,
        estack=estack,
        safe_margin_bytes=int(wm_cfg.get("safe_margin_bytes") or 64),
        warn_usage_ratio=float(wm_cfg.get("warn_usage_ratio") or 0.80),
        high_usage_ratio=float(wm_cfg.get("high_usage_ratio") or 0.95),
    )
    stats["enabled"] = True
    return stats


def format_watermark_summary(d: Dict[str, Any]) -> str:
    if not d.get("enabled", True) and "error" not in d:
        return ""
    if d.get("error"):
        return f"栈水位: 失败({d['error'][:40]})"
    depth = d.get("depth_bytes")
    avail = d.get("avail_bytes")
    risk = d.get("risk", "?")
    msp = d.get("min_sp_estimate")
    if depth is None or avail is None:
        return f"栈水位: 风险={risk}"
    msp_s = f"0x{int(msp):08X}" if msp is not None else "?"
    risk_cn = {"low": "低", "medium": "中", "high": "高"}.get(str(risk), str(risk))
    base = f"栈:深{int(depth)}/{int(avail)}B min≈{msp_s} 风险{risk_cn}"
    sc = d.get("stack_watermark_score")
    tier = d.get("stack_watermark_tier")
    if sc is not None:
        suf = f" 量化{int(sc)}"
        if tier:
            suf += f"·{tier}"
        base += suf
    return base


def testcase_stack_wm_api(
    sw: Optional[Dict[str, Any]],
    wm_cfg: Optional[dict] = None,
    fault_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """供 JudgeResponse / TestCaseResult 填充的可选字段。"""
    empty: Dict[str, Any] = {
        "stack_watermark_summary": None,
        "stack_depth_bytes": None,
        "stack_avail_bytes": None,
        "stack_min_sp_estimate": None,
        "stack_risk": None,
        "stack_watermark_score": None,
        "stack_watermark_tier": None,
    }
    if not sw:
        return empty
    if (
        sw.get("enabled") is False
        and "depth_bytes" not in sw
        and "error" not in sw
    ):
        return empty
    sw_e = enrich_stack_watermark_result(sw, wm_cfg, fault_cfg) or sw
    summary = format_watermark_summary(sw_e)
    risk = sw_e.get("risk")
    if risk not in ("low", "medium", "high"):
        risk = None
    empty.update(
        {
            "stack_watermark_summary": summary or None,
            "stack_depth_bytes": sw_e.get("depth_bytes"),
            "stack_avail_bytes": sw_e.get("avail_bytes"),
            "stack_min_sp_estimate": sw_e.get("min_sp_estimate"),
            "stack_risk": risk,
            "stack_watermark_score": sw_e.get("stack_watermark_score"),
            "stack_watermark_tier": sw_e.get("stack_watermark_tier"),
        }
    )
    return empty


def tier_from_composite_score(avg: float) -> str:
    """
    裸机无注入轮 stack_watermark_score 算术平均 → 综合档位（与分数区间对应，非深度比）。
    边界：100→安全；90≤x<100→预警；60≤x<90→危险；0≤x<60→严重。
    """
    if avg >= 100.0:
        return "安全"
    if avg >= 90.0:
        return "预警"
    if avg >= 60.0:
        return "危险"
    return "严重"


def format_stack_watermark_composite_log_line(
    normal_round_scores: list[Optional[int]],
) -> str:
    """
    仅统计无注入轮各用例的 stack_watermark_score；无有效分时返回说明行。
    """
    vals = [int(x) for x in normal_round_scores if x is not None]
    if not vals:
        return "堆栈水位线综合评分：无有效数据"
    avg = sum(vals) / len(vals)
    tier = tier_from_composite_score(avg)
    return f"堆栈水位线综合评分=={avg:.1f}（{tier}）"


def toolchain_nm_from_gdb(gdb_exe: str) -> str:
    """
    arm-none-eabi-gdb -> arm-none-eabi-nm；否则退回 nm。
    """
    g = gdb_exe.strip()
    if g.endswith("-gdb"):
        return g.replace("-gdb", "-nm", 1)
    return "arm-none-eabi-nm"
