"""
Mirror of P0002 std.c text parsing: same float32 decimal steps as parse_float_token / parse_nonneg_int_line.
"""
from __future__ import annotations

import numpy as np


def F(x) -> np.float32:
    return np.float32(x)


def f32_nan() -> np.float32:
    return np.frombuffer(np.uint32(0x7FC00000).tobytes(), dtype=np.float32)[0]


def f32_inf() -> np.float32:
    return np.frombuffer(np.uint32(0x7F800000).tobytes(), dtype=np.float32)[0]


def _skip_ws(s: str, p: int) -> int:
    n = len(s)
    while p < n and s[p] in " \t\r":
        p += 1
    return p


def _match_lc(s: str, p: int, lower_word: str) -> bool:
    for i, wc in enumerate(lower_word):
        if p + i >= len(s):
            return False
        c = s[p + i]
        if "A" <= c <= "Z":
            c = chr(ord(c) + 32)
        if c != wc:
            return False
    return True


def parse_float_token(s: str, p: int) -> tuple[np.float32, int] | None:
    """Returns (value, new_index) or None on failure."""
    p = _skip_ws(s, p)
    n = len(s)
    neg = False
    if p < n and s[p] == "+":
        p += 1
    elif p < n and s[p] == "-":
        neg = True
        p += 1

    if _match_lc(s, p, "nan"):
        p += 3
        return f32_nan(), p

    if _match_lc(s, p, "infinity"):
        p += 8
        v = f32_inf()
        return F(-v) if neg else v, p

    if _match_lc(s, p, "inf"):
        p += 3
        v = f32_inf()
        return F(-v) if neg else v, p

    intpart = F(0.0)
    has_digit = False
    while p < n and "0" <= s[p] <= "9":
        intpart = F(F(intpart * F(10.0))) + F(ord(s[p]) - 48)
        has_digit = True
        p += 1

    if p < n and s[p] == ".":
        p += 1
        scale = F(0.1)
        while p < n and "0" <= s[p] <= "9":
            intpart = F(intpart + F(F(ord(s[p]) - 48) * scale))
            scale = F(scale * F(0.1))
            has_digit = True
            p += 1

    if not has_digit:
        return None

    val = F(intpart)
    if neg:
        val = F(-val)
    return val, p


def parse_eight_float_line(s: str) -> list[np.float32] | None:
    p = 0
    vals: list[np.float32] = []
    for _ in range(8):
        r = parse_float_token(s, p)
        if r is None:
            return None
        v, p = r
        vals.append(v)
    p = _skip_ws(s, p)
    if p != len(s):
        return None
    return vals


def parse_nonneg_int_line(s: str) -> int | None:
    p = _skip_ws(s, 0)
    n = len(s)
    v = 0
    any_d = False
    while p < n and "0" <= s[p] <= "9":
        d = ord(s[p]) - 48
        if v > (2147483647 - d) // 10:
            return None
        v = v * 10 + d
        any_d = True
        p += 1
    if not any_d:
        return None
    p = _skip_ws(s, p)
    if p != n:
        return None
    return v
