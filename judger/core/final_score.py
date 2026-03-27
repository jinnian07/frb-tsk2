"""
裸机 Cortex-M UART 综合得分（0~100）：静态检查、gcov 行/分支、注入生存率、栈水位、Flash/RAM 限额内省资源。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from judger.core.static_analysis import build_clang_tidy_command

_CHECK_BRACKETS_RE = re.compile(r"\[([a-zA-Z0-9_.-]+)\]")
_DIAG_WARNING_RE = re.compile(r":\s*(warning|error)\s*:", re.IGNORECASE)


def clip01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def flash_efficiency_points(map_report: Optional[dict[str, Any]]) -> float:
    """10 * (1 - clip(used/limit))；缺数据则 0。"""
    if not map_report:
        return 0.0
    try:
        used = map_report["sections_summary_bytes"]["flash"]["total_used"]
        lim = map_report["limits"]["flash_bytes"]
        if lim is None or int(lim) <= 0:
            return 0.0
        r = clip01(float(used) / float(lim))
        return 10.0 * (1.0 - r)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def ram_efficiency_points(map_report: Optional[dict[str, Any]]) -> float:
    if not map_report:
        return 0.0
    try:
        used = map_report["sections_summary_bytes"]["ram"]["total_used"]
        lim = map_report["limits"]["ram_bytes"]
        if lim is None or int(lim) <= 0:
            return 0.0
        r = clip01(float(used) / float(lim))
        return 10.0 * (1.0 - r)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _classify_check(check: str) -> Optional[str]:
    if check.startswith("misc-misra-c2012-"):
        return "misra"
    if check.startswith("clang-analyzer-"):
        return "analyzer"
    if check == "readability-identifier-naming" or check.startswith(
        "readability-identifier-naming."
    ):
        return "naming"
    if check == "misc-unused-parameters":
        return "unused"
    if check == "misc-c-static-assert" or check.startswith("misc-c-static-assert"):
        return "static_assert"
    return None


def count_clang_tidy_violations(clang_output: str) -> Dict[str, int]:
    counts: Dict[str, int] = {
        "misra": 0,
        "analyzer": 0,
        "naming": 0,
        "unused": 0,
        "static_assert": 0,
    }
    for line in clang_output.splitlines():
        if not _DIAG_WARNING_RE.search(line):
            continue
        found = _CHECK_BRACKETS_RE.findall(line)
        if not found:
            continue
        cat = _classify_check(found[-1])
        if cat:
            counts[cat] += 1
    return counts


def static_check_points(clang_output: str, eligible: bool) -> float:
    """
    eligible=False：未正常完成静态检查 → 0 分。
    否则基准 30，五类扣分（每类 min(n*w,cap)，扣分保留一位小数）。
    """
    if not eligible:
        return 0.0
    counts = count_clang_tidy_violations(clang_output)
    rules = [
        ("misra", 2.0, 12.0),
        ("analyzer", 1.5, 9.0),
        ("naming", 0.5, 6.0),
        ("unused", 0.3, 3.0),
        ("static_assert", 0.2, 3.0),
    ]
    total_deduction = 0.0
    for key, w, cap in rules:
        n = counts.get(key, 0)
        d = min(float(n) * w, cap)
        total_deduction += round(d, 1)
    return max(0.0, round(30.0 - total_deduction, 1))


def coverage_line_points(line_pct: Optional[float]) -> float:
    if line_pct is None:
        return 0.0
    return 10.0 * clip01(float(line_pct) / 100.0)


def coverage_branch_points(branch_pct: Optional[float]) -> float:
    if branch_pct is None:
        return 0.0
    return 10.0 * clip01(float(branch_pct) / 100.0)


def survival_points(survival_rate: float, injection_total: int) -> float:
    if injection_total <= 0:
        return 0.0
    return 20.0 * clip01(float(survival_rate))


def stack_watermark_points(normal_round_scores: List[Optional[int]]) -> float:
    vals = [int(x) for x in normal_round_scores if x is not None]
    if not vals:
        return 0.0
    avg = sum(vals) / len(vals)
    return (avg / 100.0) * 10.0


def clang_tidy_run(task2_dir: Path, user_code: str) -> Tuple[bool, str]:
    """
    执行 clang-tidy 并返回 (是否计入静态 30 分档, stdout+stderr)。
    未跑成（空代码、写文件失败、未安装、超时、异常）→ (False, "").
    """
    if not user_code.strip():
        return False, ""
    temp_c_path = task2_dir / "temp_static_check.c"
    try:
        temp_c_path.write_text(user_code, encoding="utf-8")
    except OSError:
        return False, ""

    try:
        cmd, _notes = build_clang_tidy_command(
            source_file=temp_c_path,
            task2_dir=task2_dir,
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(task2_dir),
        )
        output = (result.stdout or "") + (result.stderr or "")
        return True, output
    except FileNotFoundError:
        return False, ""
    except subprocess.TimeoutExpired:
        return False, ""
    except Exception:
        return False, ""
    finally:
        try:
            if temp_c_path.is_file():
                temp_c_path.unlink()
        except OSError:
            pass


def compute_baremetal_final_score(
    *,
    static_eligible: bool,
    static_clang_output: str,
    line_pct: Optional[float],
    branch_pct: Optional[float],
    survival_rate: float,
    injection_total: int,
    normal_stack_scores: List[Optional[int]],
    map_report: Optional[dict[str, Any]],
) -> Tuple[float, Dict[str, Any]]:
    s_static = static_check_points(static_clang_output, static_eligible)
    s_line = coverage_line_points(line_pct)
    s_branch = coverage_branch_points(branch_pct)
    s_surv = survival_points(survival_rate, injection_total)
    s_stack = stack_watermark_points(normal_stack_scores)
    s_flash = flash_efficiency_points(map_report)
    s_ram = ram_efficiency_points(map_report)

    raw_total = s_static + s_line + s_branch + s_surv + s_stack + s_flash + s_ram
    total = max(0.0, min(100.0, raw_total))
    total_r = round(total, 2)

    breakdown: Dict[str, Any] = {
        "static_check": round(s_static, 1),
        "mc_dc_line_gcov": round(s_line, 2),
        "mc_dc_branch_gcov": round(s_branch, 2),
        "fault_survival": round(s_surv, 2),
        "stack_watermark": round(s_stack, 2),
        "flash_efficiency": round(s_flash, 2),
        "ram_efficiency": round(s_ram, 2),
        "total": total_r,
    }
    return total_r, breakdown


def format_final_score_log_lines(breakdown: Dict[str, Any]) -> List[str]:
    return [
        "======== 综合得分（裸机） ========",
        "说明: MC/DC 行/分支分为 gcov 课堂近似。",
        f"静态检查: {breakdown['static_check']:.1f} / 30",
        f"MC/DC·行覆盖(gcov): {breakdown['mc_dc_line_gcov']:.2f} / 10",
        f"MC/DC·分支覆盖(gcov): {breakdown['mc_dc_branch_gcov']:.2f} / 10",
        f"异常注入生存率: {breakdown['fault_survival']:.2f} / 20",
        f"堆栈水位线: {breakdown['stack_watermark']:.2f} / 10",
        f"FLASH 省资源(限额内): {breakdown['flash_efficiency']:.2f} / 10",
        f"RAM 省资源(限额内): {breakdown['ram_efficiency']:.2f} / 10",
    ]
