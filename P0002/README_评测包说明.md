# P0002 评测包说明

- **题目目录名**：`P0002`（与教学平台 `problem_id` 对齐时请使用同一标识）。
- **推荐判题模式**：`cortexm_baremetal_uart`（裸机 QEMU + UART 输入输出）。
- **结构**：与 `P0001` 相同，需包含 `题面.md`、`std.c`、`data/` 下成对 `trainXX.in` / `trainXX.out`。
- **链接**：`judger/core/baremetal_builder.py` 使用 **`-specs=nano.specs -specs=nosys.specs`**、**`-fno-math-errno`** 与 **`-lm`**，显著减小 newlib 的 RAM 占用（避免全量 newlib 下 `__malloc_av_` / `__sf` 等占满 8KB）。`baremetal/syscalls_newlib.c` 中 `_kill`/`_sbrk` **不再写 `errno`**，以免拖入大块 reent/stdio。
- **RAM / 栈**：标程已去掉 `printf` 浮点格式与 **`strtof`/`strtol`/ `nanf()`**：手写 UART 输出、手写 `N` 与 8 列浮点解析、`nan`/`inf` 用 IEEE754 常量构造，避免 newlib `strtod`/locale/`__malloc_av_` 占满 RAM 或撑爆栈。裸机链接后静态 RAM 约 **1KB 量级**（`.data`+`.bss`，依工具链略有出入），余量供栈与故障注入使用。
- **工具脚本**（可选）：`tools/p0002_parse_std.py` 与 `std.c` 的 **十进制浮点词法、`nan`/`inf`、float32 逐步运算** 一致；`tools/gen_expected.py` 用其读入每行数据并仿真，生成 `data/*.out`（避免 Python `float()` 与标程手写解析的 **1 ulp** 偏差导致 WA）。`tools/fix_refs.py` 命题写 ref 时同样依赖该解析。
