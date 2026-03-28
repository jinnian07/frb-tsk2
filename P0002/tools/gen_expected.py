"""Generate data/*.out by simulating P0002 std.c in float32 (NumPy)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from p0002_parse_std import F, parse_eight_float_line, parse_nonneg_int_line

PI_F = F(3.14159265)
RAD_TO_DEG = F(F(180.0) / PI_F)
DT = F(0.01)
ALPHA = F(0.98)
BETA = F(0.02)
ACCEL_LIM = F(2.0)
GYRO_LIM = F(250.0)
EPS = 1e-6


def fmt3_nonneg(x: float) -> str:
    """Match std.c uart_print_float_3 (non-negative RMSE), float32 rounding."""
    ax = F(abs(x))
    scaled = F(F(ax * F(1000.0)) + F(0.5))
    if scaled != scaled or float(scaled) >= 4294967000.0:
        scaled = F(0.0)
    u = int(np.uint32(float(scaled)))
    return f"{u // 1000}.{u % 1000:03d}"


def fmt1(x: float) -> str:
    """Match std.c uart_print_float_1."""
    neg = x < 0.0
    ax = -x if neg else x
    u = int(ax * 10.0 + 0.5)
    body = f"{u // 10}.{u % 10}"
    return f"-{body}" if neg else body


def is_valid(ax, ay, az, gx, gy, gz) -> bool:
    for v in (ax, ay, az, gx, gy, gz):
        fv = float(v)
        if np.isnan(fv) or np.isinf(fv):
            return False
    if abs(float(ax)) > float(ACCEL_LIM) or abs(float(ay)) > float(ACCEL_LIM) or abs(float(az)) > float(ACCEL_LIM):
        return False
    if abs(float(gx)) > float(GYRO_LIM) or abs(float(gy)) > float(GYRO_LIM) or abs(float(gz)) > float(GYRO_LIM):
        return False
    return True


def run_case(text: str) -> str:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip() != ""]
    n_line = parse_nonneg_int_line(lines[0])
    if n_line is None:
        raise ValueError("bad N line")
    n = n_line
    body = lines[1 : 1 + n]

    sp = sr = F(0.0)
    consec = 0
    drops = 0
    sum_sim = F(0.0)
    sum_hw = F(0.0)
    valid_cnt = 0

    def fusion_init():
        nonlocal sp, sr, consec
        sp = sr = F(0.0)
        consec = 0

    for line in body:
        nums = parse_eight_float_line(line)
        if nums is None:
            raise ValueError(f"bad data line: {line!r}")
        ax, ay, az, gx, gy, gz, ref_p, ref_r = nums

        if not is_valid(ax, ay, az, gx, gy, gz):
            drops += 1
            consec += 1
            if consec >= 5:
                fusion_init()
            continue

        consec = 0

        ay2 = F(ay * ay)
        az2 = F(az * az)
        denom = F(np.sqrt(float(F(ay2 + az2))))
        pitch_acc = F(np.arctan2(float(-ax), float(denom)) * float(RAD_TO_DEG))
        roll_acc = F(np.arctan2(float(ay), float(az)) * float(RAD_TO_DEG))

        sp = F(ALPHA * F(sp + F(gx * DT)) + BETA * pitch_acc)
        sr = F(ALPHA * F(sr + F(gy * DT)) + BETA * roll_acc)
        pitch = sp
        roll = sr

        ep = F(pitch - ref_p)
        er = F(roll - ref_r)
        sum_sim = F(sum_sim + F(ep * ep + er * er))

        ep = F(pitch_acc - ref_p)
        er = F(roll_acc - ref_r)
        sum_hw = F(sum_hw + F(ep * ep + er * er))

        valid_cnt += 1

    if valid_cnt == 0:
        rmse_sim = 0.0
        rmse_hw = 0.0
    else:
        m_sim = F(sum_sim / F(float(valid_cnt)))
        m_hw = F(sum_hw / F(float(valid_cnt)))
        rmse_sim = float(np.sqrt(np.float32(m_sim)))
        rmse_hw = float(np.sqrt(np.float32(m_hw)))

    if n > 0:
        a = abs(rmse_sim - rmse_hw)
        mx = max(rmse_sim, rmse_hw, EPS)
        diff_pct = (a / mx) * 100.0
    else:
        diff_pct = 0.0

    spec = "within spec" if diff_pct <= 10.0 else "not within spec"

    out_lines = [
        f"[FUSION] RMSE_sim: {fmt3_nonneg(rmse_sim)} deg",
        f"[FUSION] RMSE_hw: {fmt3_nonneg(rmse_hw)} deg",
        f"[FUSION] Diff: {fmt1(diff_pct)}% ({spec})",
    ]
    if n > 0:
        pct = 100.0 * drops / n
        out_lines.append(f"[FUSION] Abnormal drops: {drops} times ({fmt1(pct)}% of total)")
    else:
        out_lines.append("[FUSION] Abnormal drops: 0 times (0.0% of total)")

    return "\n".join(out_lines) + "\n"


def main():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    if not data_dir.is_dir():
        print("missing data dir", data_dir, file=sys.stderr)
        sys.exit(1)
    for p in sorted(data_dir.glob("*.in")):
        outp = run_case(p.read_text(encoding="utf-8"))
        p.with_suffix(".out").write_text(outp, encoding="utf-8", newline="\n")
        print(p.name)


if __name__ == "__main__":
    main()
