"""Rewrite data/*.in: set ref to float32 midpoint of fusion vs accel angle each valid step (RMSE_sim == RMSE_hw)."""
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


def simulate_row(ax, ay, az, gx, gy, gz, sp, sr):
    ay2 = F(ay * ay)
    az2 = F(az * az)
    denom = F(np.sqrt(float(F(ay2 + az2))))
    pitch_acc = F(np.arctan2(float(-ax), float(denom)) * float(RAD_TO_DEG))
    roll_acc = F(np.arctan2(float(ay), float(az)) * float(RAD_TO_DEG))
    sp = F(ALPHA * F(sp + F(gx * DT)) + BETA * pitch_acc)
    sr = F(ALPHA * F(sr + F(gy * DT)) + BETA * roll_acc)
    return sp, sr, pitch_acc, roll_acc


def fix_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip() != ""]
    n = parse_nonneg_int_line(lines[0])
    if n is None:
        raise ValueError(path.name)
    body = lines[1 : 1 + n]

    sp = sr = F(0.0)
    consec = 0

    def fusion_init():
        nonlocal sp, sr, consec
        sp = sr = F(0.0)
        consec = 0

    out_rows: list[str] = []
    for line in body:
        parts = line.split()
        parsed = parse_eight_float_line(line)
        if parsed is None:
            raise ValueError(line)
        ax, ay, az, gx, gy, gz, _, _ = parsed

        if not is_valid(ax, ay, az, gx, gy, gz):
            consec += 1
            if consec >= 5:
                fusion_init()
            out_rows.append(f"{parts[0]} {parts[1]} {parts[2]} {parts[3]} {parts[4]} {parts[5]} 0.0 0.0")
            continue

        consec = 0
        sp, sr, pa, ra = simulate_row(ax, ay, az, gx, gy, gz, sp, sr)
        rp = F(F(sp + pa) / F(2.0))
        rr = F(F(sr + ra) / F(2.0))
        out_rows.append(
            f"{parts[0]} {parts[1]} {parts[2]} {parts[3]} {parts[4]} {parts[5]} {float(rp):.6g} {float(rr):.6g}"
        )

    new_text = str(n) + "\n" + "\n".join(out_rows) + "\n"
    path.write_text(new_text, encoding="utf-8", newline="\n")


def main():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    for p in sorted(data_dir.glob("*.in")):
        fix_file(p)
        print("fixed", p.name)


if __name__ == "__main__":
    main()
