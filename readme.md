# task2（嵌入式 OJ）FastAPI 改造

## 项目简介
该项目将原有嵌入式在线判题系统封装为 FastAPI HTTP API。核心能力包括：
- 用户提交 C 代码
- 通过远端 SSH 上传编译并在模拟环境中运行（QEMU + 远端执行）
- 对比标准输出进行判题（正常测试 + 故障注入测试）
- 统计异常注入后的恢复生存率（`survival_rate`）

HTTP API：`POST /api/v1/judge`

## 启动命令
方式一：使用 uvicorn
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

方式二：直接运行 `app/main.py`
```bash
python app/main.py
```
启动后打印并可访问：
- Swagger UI: `http://localhost:8000/docs`

## API 测试示例
```bash
curl -X POST "http://localhost:8000/api/v1/judge" -H "Content-Type: application/json" -d '{"problem_id": "P0001", "code": "用户提交的C代码字符串"}'
```

> 注意：`code` 是完整的 C 源码字符串。

## 请求/响应契约
- 请求：`{"problem_id": "P0001", "code": "用户提交的C代码字符串"}`
- 响应：
```json
{
  "overall_result": "AC",
  "test_cases": [
    {"name": "1.in", "status": "AC", "time_ms": 100, "info": "通过"}
  ],
  "survival_rate": 85.5,
  "total_tests": 4,
  "successful_recoveries": 3,
  "resource_usage_summary": "FLASH占用率 0.60% (784/131072 bytes); RAM占用率 12.34% (1012/8192 bytes)",
  "resource_usage": { "limits": { "source": "linker_script", "flash_bytes": 131072, "ram_bytes": 8192 } }
}
```

裸机模式 `judge_mode=cortexm_baremetal_uart` 且编译成功时，`resource_usage_summary` / `resource_usage` 由 `firmware.map` 解析得到（静态估算，无运行时栈深）；普通 C 模式或未解析成功时二者为 `null`。

## GUI 静态检查（嵌入式 C）

点击工具栏「提交评测」时，先完成与普通 C / 裸机一致的评测流程；结束后对**编辑器中的代码**自动运行 `clang-tidy`，**静态检查整块结果写在当次运行日志末尾**。编译参数与裸机固件一致（`-target arm-none-eabi`、`--config-file task2/.clang-tidy`、`-mcpu=cortex-m3`、`-ffreestanding`、`-I baremetal` 等）。

**依赖：**

- `clang-tidy`（LLVM），需在 `PATH` 中。
- **推荐**安装 [Arm GNU Toolchain](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain)（`arm-none-eabi-gcc`），以便自动加入 `stdint.h`、`stdio.h` 等头路径。若未安装，会尝试使用本机 `clang` 的 `-print-resource-dir` 下内置头**降级**；仍失败时日志会提示安装工具链。

临时文件写入 `task2/temp_static_check.c`（运行结束后删除），工作目录固定为 `task2/`，保证能读取 `task2/.clang-tidy`。

若工程根下存在 `compile_commands.json`，可自行对**已收录**的源文件执行：

`clang-tidy -p <task2目录> P0002/std.c`

## GUI 课堂覆盖率（gcov，仅裸机评测）

- **配置**：在 `config.json` 顶层设置 `"enable_coverage_embedded": true`。默认 `false`，避免额外编译耗时。
- **触发时机**：仅在 **「裸机 Cortex-M UART」** 评测**成功跑完全部测例流程**后执行；**不参与 AC/WA**，结果写入日志并弹出摘要对话框。
- **实现方式（宿主近似）**：QEMU stm32vldiscovery 镜像侧**无通用文件系统写 `.gcda`**，因此采用课堂折中——用本机 **`gcc --coverage`** 将「去掉 `main` 的题解 + `uart_oj_rx_poll.c` + `coverage_host_stubs.c` + `coverage_host_driver.c`」链接为宿主程序，按 `data/*.in` 生成与 `BareMetalUartRunner` 一致的 UART 字节流，逐测例运行以合并 `.gcda`，再调用 **`gcov -b`** 汇总**行覆盖率、分支覆盖率**。
- **与 DO-178C MC/DC**：此处为 **gcov 行/分支%**，**不是**形式化 MC/DC；文档与弹窗中已标明「课堂近似」。
- **依赖**：`PATH` 中可找到 **`gcc` 与 `gcov`**（Windows 常见为 MSYS2/MinGW-w64，与 `arm-none-eabi-gcc` 可并存）。
- **命令行自检**（在 `task2/` 下）：

```bash
python -c "from judger.core.coverage_embedded import self_check; print(self_check())"
```

## 资源占用偏差（GNU ld .map）

- 目标：从 `.map` 提取 Flash/RAM 占用并计算 `used/limit` 百分比。
- 脚本：`judger/core/map_resource_usage.py`（可独立运行）。
- 输入：
  - `--map`：GNU ld map 文件（必填）
  - `--linker-script`：可选，从 `MEMORY {}` 读取 `FLASH/RAM` 限制
  - `--limits-json`：可选，JSON 覆盖限制（`flash_bytes`、`ram_bytes`）
- 输出：一行人类可读摘要 + JSON（可用 `--json-out` 落盘）

示例：

```bash
cd task2
python -m judger.core.map_resource_usage \
  --map .temp/job_xxx/firmware/firmware.map \
  --linker-script baremetal/linker_stm32vldiscovery.ld \
  --json-out .temp/job_xxx/firmware/resource_usage.json \
  --no-sections
```

分类口径（静态）：

- Flash：`.text/.isr_vector` 归代码，`.rodata/.ARM.extab/.ARM.exidx` 归只读数据，`.data` 的 LMA 计入 Flash。
- RAM：`.data`（VMA 在 RAM）、`.bss`、`NOLOAD` 和其他 RAM 段计入 RAM 占用。
- 栈/堆：仅基于 map 符号静态推断（例如 `_estack`、`_ebss`、`__heap_base`），不能代表运行时峰值。

限制说明：

- 仅靠 `.map` 无法得到运行时真实栈深和真实堆用量，输出中会标记为静态估算。
- 若 map 缺少 `Memory Configuration` 或符号，脚本仍会输出 JSON，但对应字段可能为 `null/unknown`。

## 目录映射表（原 `core/` → FastAPI）
| 原文件路径（task2/core/） | FastAPI 模块（task2/app/） |
|---|---|
| `core/oj_engine.py` | `app/core/oj_engine.py` |
| `core/ssh_executor.py` | `app/core/ssh_executor.py` |
| `core/qemu_manager.py` | `app/core/qemu_manager.py` |
| `core/config.py` | `app/core/config.py` |
| `core/project_manager.py` | `app/core/project_manager.py` |

FastAPI 判题服务入口：
- 路由：`app/api/judge_router.py`
- 业务：`app/services/judge_service.py`（`judge()` 实现）