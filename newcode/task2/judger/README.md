# judger — 评测机库（无 HTTP）

从 **`task2` 根目录** 运行工程（`python main.py`、`uvicorn` 指向 `api.main` 等），保证当前工作目录或 `sys.path` 包含 `task2`，即可：

```python
from pathlib import Path
from judger import JudgeService

svc = JudgeService(task2_root=Path(__file__).resolve().parent)  # 传入 task2 根
resp = svc.judge("P0001", code, judge_mode="c")
```

- **裸机资源**：`baremetal/` 仍在 `task2/baremetal/`，由 `JudgeService` 的 `task2_root` 与 `BareMetalBuilder(runtime_dir=task2_root / "baremetal")` 定位，不在本包内复制。
- **配置**：默认读取 `task2_root/config.json`、`task2_root/fault_injection_config.json`。

## 旧路径 → 新路径（迁移对照）

| 原路径（已删除） | 新路径 |
|------------------|--------|
| `task2/core/oj_engine.py` | `judger/core/oj_engine.py` |
| `task2/core/config.py` | `judger/core/config.py` |
| `task2/core/project_manager.py` | `judger/core/project_manager.py` |
| `task2/core/ssh_executor.py` | `judger/core/ssh_executor.py` |
| `task2/core/qemu_manager.py` | `judger/core/qemu_manager.py` |
| `task2/core/gdb_memory_inject.py` | `judger/core/gdb_memory_inject.py` |
| `task2/core/fault_injection_config.py` | `judger/core/fault_injection_config.py` |
| `task2/core/baremetal_*.py` | `judger/core/baremetal_*.py` |
| `task2/core/stack_watermark.py` | `judger/core/stack_watermark.py` |
| `task2/core/coverage_embedded.py` | `judger/core/coverage_embedded.py` |
| `task2/core/static_analysis.py` | `judger/core/static_analysis.py` |
| `api/judge_service.py`（实现） | `judger/judge_service.py` |
| `api/schemas.py`（定义） | `judger/schemas.py`（`api/schemas.py` 仅 re-export） |

`task2/core/` 目录下原 Python 模块已移除；`app/core/*` 改为对 `judger.core` 的薄 re-export。

## 命令行覆盖率自检

```bash
cd task2
python -c "from judger.core.coverage_embedded import self_check; print(self_check())"
```
