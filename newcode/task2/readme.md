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
  "successful_recoveries": 3
}
```

## GUI 静态检查（嵌入式 C）

工具栏「静态检查」对**编辑器中的代码**运行 `clang-tidy`，编译参数与裸机固件一致（`-target arm-none-eabi`、`--config-file task2/.clang-tidy`、`-mcpu=cortex-m3`、`-ffreestanding`、`-I baremetal` 等）。

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