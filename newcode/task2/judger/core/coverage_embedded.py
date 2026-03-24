"""
课堂级嵌入式代码覆盖率（宿主近似）：GCC --coverage + gcov。

裸机 QEMU 下无可靠文件系统写 .gcda，故在 Windows/Linux 上用宿主 gcc 编译
「剥离 main 的用户 TU + uart_oj_rx_poll + 本机 UART 桩 + 独立 driver main」，
按题目 data 的输入字节（与 BareMetalUartRunner 一致的十六进制/文本规则）逐测例运行，
合并 .gcda 后调用 gcov 汇总行/分支覆盖。与真实 Flash 上执行路径可能不同，见 readme。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from judger.core.baremetal_uart_runner import _normalize_uart_input


def _strip_main_function(source: str) -> Optional[str]:
    """
    移除首个非 static 的全局 int main(...){ ... }，供链接 coverage_host_driver 的 main。
    """
    pos = 0
    while True:
        m = re.search(r"(?<![a-zA-Z0-9_])int\s+main\s*\(", source[pos:])
        if not m:
            return None
        abs_m = pos + m.start()
        line_start = source.rfind("\n", 0, abs_m) + 1
        before = source[line_start:abs_m]
        if re.search(r"\bstatic\b", before):
            pos = abs_m + 4
            continue

        sub = source[abs_m:]
        paren = 0
        j = 0
        while j < len(sub):
            if sub[j] == "(":
                paren += 1
            elif sub[j] == ")":
                paren -= 1
                if paren == 0:
                    j += 1
                    break
            j += 1
        else:
            return None

        rest = sub[j:]
        brace_at = rest.find("{")
        if brace_at < 0:
            return None
        start_brace = abs_m + j + brace_at
        depth = 0
        i = start_brace
        while i < len(source):
            c = source[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    while end < len(source) and source[end] in " \t\r\n":
                        end += 1
                    return source[:line_start] + source[end:]
            i += 1
        return None


def _host_gcc() -> Optional[str]:
    for name in ("gcc", "gcc.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _host_gcov() -> Optional[str]:
    gcc = _host_gcc()
    if gcc:
        parent = Path(gcc).parent
        for gname in ("gcov", "gcov.exe"):
            cand = parent / gname
            if cand.is_file():
                return str(cand)
    for gname in ("gcov", "gcov.exe"):
        p = shutil.which(gname)
        if p:
            return p
    return None


def _parse_gcov_summary(text: str) -> Dict[str, Any]:
    line_pct: Optional[float] = None
    line_total: Optional[int] = None
    branch_pct: Optional[float] = None
    branch_total: Optional[int] = None

    lm = re.search(r"Lines executed:\s*([\d.]+)%\s+of\s+(\d+)", text)
    if lm:
        line_pct = float(lm.group(1))
        line_total = int(lm.group(2))
    bm = re.search(r"Branches executed:\s*([\d.]+)%\s+of\s+(\d+)", text)
    if bm:
        branch_pct = float(bm.group(1))
        branch_total = int(bm.group(2))

    return {
        "line_pct": line_pct,
        "line_total": line_total,
        "branch_pct": branch_pct,
        "branch_total": branch_total,
    }


def run_embedded_host_coverage(
    *,
    prepared_user_c: str,
    task2_root: Path,
    problem_id: str,
    case_in_paths: List[Path],
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    构建宿主覆盖率程序，按测例输入运行，gcov 汇总用户 TU。

    Returns:
        ok: bool
        summary_text: str (for log / messagebox)
        detail: dict from _parse_gcov_summary + paths
    """
    _log = log or (lambda _s: None)

    gcc = _host_gcc()
    gcov = _host_gcov()
    if not gcc:
        return {
            "ok": False,
            "summary_text": "未找到宿主 gcc，跳过课堂覆盖率。",
            "detail": {},
        }
    if not gcov:
        return {
            "ok": False,
            "summary_text": "未找到 gcov，跳过课堂覆盖率（请安装 MinGW/MSYS2 或 Linux gcc）。",
            "detail": {},
        }

    stripped = _strip_main_function(prepared_user_c)
    if stripped is None:
        return {
            "ok": False,
            "summary_text": "未能剥离 int main()，跳过宿主覆盖率。",
            "detail": {},
        }

    work = (
        task2_root
        / ".temp"
        / f"cov_host_{problem_id}_{os.getpid()}"
    )
    work.mkdir(parents=True, exist_ok=True)

    baremetal = task2_root / "baremetal"
    user_c = work / "user_for_cov.c"
    user_c.write_text(stripped, encoding="utf-8")

    objs: List[Path] = []
    cflags = [
        "-O0",
        "-g",
        "--coverage",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Wno-unused-parameter",
        f"-I{baremetal.resolve()}",
    ]

    sources = [
        (user_c, work / "user_for_cov.o"),
        (baremetal / "uart_oj_rx_poll.c", work / "uart_oj_rx_poll.o"),
        (baremetal / "coverage_host_stubs.c", work / "coverage_host_stubs.o"),
        (baremetal / "coverage_host_driver.c", work / "coverage_host_driver.o"),
    ]

    try:
        for src, obj in sources:
            if not src.is_file():
                return {
                    "ok": False,
                    "summary_text": f"缺少源文件: {src}",
                    "detail": {},
                }
            cmd = [gcc, *cflags, "-c", str(src.resolve()), "-o", str(obj.resolve())]
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(work),
                timeout=120,
            )
            if r.returncode != 0:
                _log(r.stderr or r.stdout or "gcc compile failed")
                return {
                    "ok": False,
                    "summary_text": f"宿主覆盖率编译失败: {(r.stderr or r.stdout or '')[:400]}",
                    "detail": {},
                }
            objs.append(obj)

        out_exe = work / ("cov_host.exe" if sys.platform == "win32" else "cov_host")
        link = [gcc, "--coverage", "-o", str(out_exe.resolve())] + [
            str(o.resolve()) for o in objs
        ]
        r = subprocess.run(
            link,
            capture_output=True,
            text=True,
            cwd=str(work),
            timeout=120,
        )
        if r.returncode != 0:
            _log(r.stderr or r.stdout or "gcc link failed")
            return {
                "ok": False,
                "summary_text": f"宿主覆盖率链接失败: {(r.stderr or r.stdout or '')[:400]}",
                "detail": {},
            }

        if not out_exe.is_file():
            alt = list(work.glob("cov_host*"))
            out_exe = alt[0] if alt else out_exe

        for pat in ("*.gcda", "*.gcov"):
            for p in work.glob(pat):
                try:
                    p.unlink()
                except OSError:
                    pass

        for idx, in_path in enumerate(case_in_paths):
            raw = in_path.read_text(encoding="utf-8", errors="ignore")
            payload = _normalize_uart_input(raw)
            bin_path = work / f"case_{idx}.bin"
            bin_path.write_bytes(payload)
            rr = subprocess.run(
                [str(out_exe.resolve()), str(bin_path.resolve())],
                capture_output=True,
                text=True,
                cwd=str(work),
                timeout=30,
            )
            if rr.returncode != 0:
                _log(f"覆盖率运行 case {idx} 退出码 {rr.returncode}")

        gr = subprocess.run(
            [gcov, "-b", str(user_c.name)],
            capture_output=True,
            text=True,
            cwd=str(work),
            timeout=60,
        )
        out_txt = (gr.stdout or "") + (gr.stderr or "")
        parsed = _parse_gcov_summary(out_txt)
        lines = [
            f"[课堂覆盖率·宿主 gcov 近似] 题目 {problem_id}",
            "说明: 在宿主机 gcc 上近似统计，与 QEMU 内执行路径可能不同。",
        ]
        if parsed["line_pct"] is not None:
            lines.append(
                f"行覆盖: {parsed['line_pct']:.2f}% (可统计行 {parsed['line_total']})"
            )
        if parsed["branch_pct"] is not None:
            lines.append(
                f"分支覆盖: {parsed['branch_pct']:.2f}% (分支数 {parsed['branch_total']})"
            )
        if parsed["line_pct"] is None and parsed["branch_pct"] is None:
            lines.append("gcov 未解析到摘要，原始输出见日志。")
            lines.append(out_txt[:800])

        summary = "\n".join(lines)
        _log(summary)
        if out_txt and ("Lines executed" not in out_txt):
            _log(out_txt[:1500])

        return {
            "ok": True,
            "summary_text": summary,
            "detail": {**parsed, "work_dir": str(work)},
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "summary_text": "宿主覆盖率步骤超时。",
            "detail": {},
        }
    except OSError as e:
        return {
            "ok": False,
            "summary_text": f"宿主覆盖率 IO/进程错误: {e}",
            "detail": {},
        }


def self_check(task2_root: Optional[Path] = None) -> Dict[str, Any]:
    """供命令行自检：对 P0002/std.c 跑一轮最小覆盖率。"""
    root = task2_root or Path(__file__).resolve().parents[2]
    std = root / "P0002" / "std.c"
    text = std.read_text(encoding="utf-8")
    from judger.core.baremetal_code_prep import prepare_baremetal_uart_code

    prep = prepare_baremetal_uart_code(text)
    data = root / "P0002" / "data"
    ins = sorted(data.glob("*.in"))
    return run_embedded_host_coverage(
        prepared_user_c=prep,
        task2_root=root,
        problem_id="P0002",
        case_in_paths=ins,
        log=print,
    )
