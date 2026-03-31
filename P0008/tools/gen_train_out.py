"""Generate trainXX.out for P0008 from fixed rounding rules (must match std.c)."""
from __future__ import annotations

SIM_N, SIM_L = 248, 11


def red_x10(hw_n: int, hw_lp: int) -> int:
    d = hw_n - hw_lp
    q = (d * 1000) // hw_n
    r = (d * 1000) % hw_n
    if r * 2 >= hw_n:
        q += 1
    return q


def diff_x10(hw: int, sim: int) -> int:
    d = hw - sim
    sign = 1 if d >= 0 else -1
    ad = abs(d)
    q = (ad * 1000) // sim
    r = (ad * 1000) % sim
    if r * 2 >= sim:
        q += 1
    return sign * q


def fmt_ma(t: int) -> str:
    return f"{t // 10}.{t % 10}"


def fmt_pct(q: int) -> str:
    if q >= 0:
        return f"+{q // 10}.{q % 10}%"
    q = -q
    return f"-{q // 10}.{q % 10}%"


def gen_out(hw_n: int, hw_lp: int) -> str:
    lines: list[str] = []
    for _ in range(2):
        for k in range(3):
            lines.append(f"Temp: 25.{1 + k} C")
    r10 = red_x10(hw_n, hw_lp)
    dn = diff_x10(hw_n, SIM_N)
    dl = diff_x10(hw_lp, SIM_L)
    lines.append("==== Power Consumption Report ====")
    lines.append(f"Normal mode avg current: {fmt_ma(hw_n)} mA")
    lines.append(f"Low power mode avg current: {fmt_ma(hw_lp)} mA")
    rr = abs(r10)
    lines.append(f"Reduction: {rr // 10}.{rr % 10}%")
    lines.append("Simulation vs Hardware difference: ")
    lines.append(
        f"  Sim Normal: {fmt_ma(SIM_N)} mA, HW Normal: {fmt_ma(hw_n)} mA (diff {fmt_pct(dn)})"
    )
    lines.append(
        f"  Sim LP: {fmt_ma(SIM_L)} mA, HW LP: {fmt_ma(hw_lp)} mA (diff {fmt_pct(dl)})"
    )
    lines.append(
        "Analysis: QEMU not cycle-accurate; UART and LDO not in energy model; meter noise on HW."
    )
    return "\n".join(lines) + "\n"


PAIRS = [
    (253, 12),
    (120, 30),
    (400, 50),
    (185, 21),
    (300, 45),
    (220, 10),
    (150, 25),
    (500, 80),
    (90, 20),
    (350, 60),
]

INS = [
    ("25.3", "1.2"),
    ("12.0", "3.0"),
    ("40.0", "5.0"),
    ("18.5", "2.1"),
    ("30.0", "4.5"),
    ("22.0", "1.0"),
    ("15.0", "2.5"),
    ("50.0", "8.0"),
    ("9.0", "2.0"),
    ("35.0", "6.0"),
]

if __name__ == "__main__":
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    for idx, ((a, b), (hn, hlp)) in enumerate(zip(INS, PAIRS), start=1):
        name = f"train{idx:02d}"
        (data / f"{name}.in").write_text(f"{a}\n{b}\n", encoding="utf-8")
        (data / f"{name}.out").write_text(gen_out(hn, hlp), encoding="utf-8")
        print(name, "OK")
