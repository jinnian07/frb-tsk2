from __future__ import annotations

import re


_PROCESS_FRAME_PROTO = "uint8_t process_frame(uint8_t *raw, uint8_t len);"


def _has_stddef_include(code: str) -> bool:
    return "#include <stddef.h>" in code or "#include <stddef>" in code


def _needs_null_fix(code: str) -> bool:
    # Heuristic: only patch when NULL is referenced.
    # Keep it simple: if user code uses "NULL" but forgot the header, inject it.
    return ("NULL" in code) and (not _has_stddef_include(code))


def _has_process_frame_call(code: str) -> bool:
    return "process_frame(" in code


def _has_process_frame_proto(code: str) -> bool:
    # Detect a forward declaration with the expected signature.
    # Covers both "uint8_t process_frame(...);" and whitespace variations.
    pattern = re.compile(
        r"uint8_t\s+process_frame\s*\(\s*uint8_t\s*\*\s*raw\s*,\s*uint8_t\s+len\s*\)\s*;",
        re.MULTILINE,
    )
    return pattern.search(code) is not None


def _insertion_index_after_includes(code: str) -> int:
    lines = code.splitlines(True)  # keep newline chars
    include_idxs = [
        i
        for i, line in enumerate(lines)
        if line.lstrip().startswith("#include")
    ]
    if not include_idxs:
        return 0
    last_include = max(include_idxs)
    return last_include + 1


def _needs_stub_main(code: str) -> bool:
    """
    Reset_Handler calls external `main`. If user code only has ISR/helpers
    (e.g. UART_IRQHandler) and no global main, the link step fails with
    undefined reference to `main`.

    Do not inject if:
    - user already has a non-static `int main(...) {` definition (line-start heuristic)
    - opening brace on the next line after `int main(...)` (common style)
    - user uses `static int main` (invalid for C runtime; avoid duplicate identifier)
    """
    if re.search(r"^\s*static\s+int\s+main\b", code, re.MULTILINE):
        return False
    if re.search(
        r"^\s*int\s+main\s*\([^)]*\)\s*\{",
        code,
        re.MULTILINE,
    ):
        return False
    if re.search(
        r"^\s*int\s+main\s*\([^)]*\)\s*\n\s*\{",
        code,
        re.MULTILINE,
    ):
        return False
    return True


_STUB_MAIN = """\
/* OJ bare-metal: entry required by startup Reset_Handler -> main() */
int main(void)
{
    while (1) {
    }
    return 0;
}
"""


def prepare_baremetal_uart_code(code: str) -> str:
    """
    Prepare user code for `cortexm_baremetal_uart` bare-metal UART mode.

    Inject minimal missing declarations/includes to avoid common C compilation errors:
      1) If NULL is used but <stddef.h> is missing -> inject it.
      2) If process_frame(...) is called before its definition but no prototype exists ->
         inject a prototype after the include block.
      3) If there is no global `int main(void) { ... }` definition -> append a minimal
         main() so the linker satisfies Reset_Handler's call to main().

    This function is intentionally conservative and only patches when required.
    """
    if not code:
        return code

    insertion_lines: list[str] = []

    if _needs_null_fix(code):
        insertion_lines.append("#include <stddef.h>\n")

    if _has_process_frame_call(code) and (not _has_process_frame_proto(code)):
        insertion_lines.append(_PROCESS_FRAME_PROTO + "\n")

    if insertion_lines:
        idx = _insertion_index_after_includes(code)
        lines = code.splitlines(True)  # keep newline chars
        code = "".join(lines[:idx] + insertion_lines + lines[idx:])

    if _needs_stub_main(code):
        code = code.rstrip() + "\n\n" + _STUB_MAIN

    return code

