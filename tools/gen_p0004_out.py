#!/usr/bin/env python3
"""
P0004 discrete-event reference — must match P0004/std.c scheduler semantics.
Generates P0004/data/*.out from *.in for bare-metal UART OJ.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskStats:
    sum_resp: int = 0
    cnt: int = 0
    max_resp: int = 0
    min_resp: int = 99999


@dataclass
class Task:
    tid: int
    base_prio: int
    period: int
    wcet_base: int
    rem: int = 0
    release_t: int = 0
    blocked: bool = False
    st: TaskStats = field(default_factory=TaskStats)


def parse_input(text: str) -> tuple[bool, bool, int]:
    reset_stats = "r" in text.lower()
    heavy_m = "d" in text.lower()
    nums = [int(m.group(0)) for m in re.finditer(r"\d+", text)]
    run_ticks = nums[-1] if nums else 100
    run_ticks = max(1, min(5000, run_ticks))
    return reset_stats, heavy_m, run_ticks


def simulate(n_ticks: int, heavy_m: bool) -> tuple[list[Task], int, int]:
    m_wcet = 3 + (3 if heavy_m else 0)
    tasks = [
        Task(0, 1, 20, 4),
        Task(1, 2, 15, m_wcet),
        Task(2, 3, 10, 2),
    ]
    mutex_owner: int | None = None
    dl = 0

    def eff_prio(t: Task) -> int:
        p = t.base_prio
        if t.tid == 0 and mutex_owner == 0:
            h = tasks[2]
            if h.blocked:
                p = max(p, h.base_prio)
        return p

    def start_job(t: Task, tick: int) -> None:
        t.rem = t.wcet_base
        t.release_t = tick
        t.blocked = False

    for t in tasks:
        t.rem = t.wcet_base
        t.release_t = 0
        t.blocked = False

    for tick in range(1, n_ticks + 1):
        for t in tasks:
            if tick % t.period == 0:
                start_job(t, tick)

        for t in tasks:
            t.blocked = False

        h, l = tasks[2], tasks[0]
        if h.rem == 2:
            if mutex_owner is not None and mutex_owner != 2:
                h.blocked = True
        if l.rem == 4:
            if mutex_owner is not None and mutex_owner != 0:
                l.blocked = True

        candidates = [t for t in tasks if t.rem > 0 and not t.blocked]
        if not candidates:
            continue
        candidates.sort(key=lambda x: (-eff_prio(x), -x.base_prio, x.tid))
        run = candidates[0]

        if run.tid == 0:
            if run.rem == 4:
                mutex_owner = 0
            run.rem -= 1
            if run.rem == 2:
                mutex_owner = None
            if run.rem == 0:
                r = tick - run.release_t
                s = run.st
                s.sum_resp += r
                s.cnt += 1
                s.max_resp = max(s.max_resp, r)
                s.min_resp = min(s.min_resp, r)
        elif run.tid == 1:
            run.rem -= 1
            if run.rem == 0:
                r = tick - run.release_t
                s = run.st
                s.sum_resp += r
                s.cnt += 1
                s.max_resp = max(s.max_resp, r)
                s.min_resp = min(s.min_resp, r)
        else:
            if run.rem == 2:
                mutex_owner = 2
            elif run.rem == 1:
                mutex_owner = None
            run.rem -= 1
            if run.rem == 0:
                r = tick - run.release_t
                if r > 10:
                    dl += 1
                s = run.st
                s.sum_resp += r
                s.cnt += 1
                s.max_resp = max(s.max_resp, r)
                s.min_resp = min(s.min_resp, r)

    hst = tasks[2].st
    hj = (hst.max_resp - hst.min_resp) * 100 if hst.cnt >= 2 else 0
    return tasks, dl, hj


def _ms_x100_to_str(v: int) -> str:
    neg = v < 0
    if neg:
        v = -v
    ip = v // 100
    fp = v % 100
    return ("-" if neg else "") + f"{ip}.{fp:02d}"


def format_report(tasks: list[Task], deadlock_count: int, h_jitter_x100: int, n_ticks: int) -> str:
    h_periods = max(1, 1 + (n_ticks // 10))
    pct_x10 = (deadlock_count * 1000 + h_periods // 2) // h_periods

    lines = []
    for tid, stack_b in ((2, 96), (1, 112), (0, 104)):
        t = next(x for x in tasks if x.tid == tid)
        s = t.st
        tag = "[H]" if tid == 2 else ("[M]" if tid == 1 else "[L]")
        if s.cnt == 0:
            lines.append(f"{tag} avg_resp=0.00ms max=0.00ms jitter=0.00ms stack={stack_b}B")
        else:
            avg_x100 = (s.sum_resp * 100 + s.cnt // 2) // s.cnt
            max_x100 = s.max_resp * 100
            jit_x100 = (s.max_resp - s.min_resp) * 100 if s.cnt >= 2 else 0
            lines.append(
                f"{tag} avg_resp={_ms_x100_to_str(avg_x100)}ms max={_ms_x100_to_str(max_x100)}ms "
                f"jitter={_ms_x100_to_str(jit_x100)}ms stack={stack_b}B"
            )
    lines.append(
        f"[Deadlock] count={deadlock_count} / {h_periods} periods ({pct_x10 // 10}.{pct_x10 % 10}%)"
    )
    lines.append(
        f"[Jitter Ratio H] sim_hw_jitter/sim_sim_jitter = {_ms_x100_to_str(h_jitter_x100)}/"
        f"{_ms_x100_to_str(h_jitter_x100)} = 1.00 (OK)"
    )
    return "\n".join(lines) + "\n"


def expected_output(in_text: str) -> str:
    _reset, heavy, n = parse_input(in_text)
    tasks, dl, hj = simulate(n, heavy)
    return format_report(tasks, dl, hj, n)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate P0004 train*.out from train*.in")
    ap.add_argument("--data-dir", type=Path, required=True)
    args = ap.parse_args()
    d = args.data_dir
    for p in sorted(d.glob("train*.in")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        p.with_suffix(".out").write_text(expected_output(text), encoding="utf-8")
        print(f"wrote {p.with_suffix('.out')}")


if __name__ == "__main__":
    main()
