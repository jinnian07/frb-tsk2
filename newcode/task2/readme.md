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