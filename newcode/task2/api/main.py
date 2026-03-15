# FastAPI API contract (HTTP):
#
# 1) Request
# POST /api/v1/judge
# Content-Type: application/json
#
# Request JSON:
# {
#   "problem_id": "P0001",
#   "code": "用户提交的C代码字符串"
# }
#
# 2) Response
# Response JSON:
# {
#   "overall_result": "AC",            # 总体结果（AC/WA/RE/TLE）
#   "test_cases": [                   # 各测试用例结果（正常+故障注入）
#     {"name": "1.in", "status": "AC", "time_ms": 100, "info": "通过"},
#     {"name": "2.in", "status": "WA", "time_ms": 120, "info": "答案错误"}
#   ],
#   "survival_rate": 85.5,            # 异常注入生存率（%）
#   "total_tests": 4,                 # 总测试次数（正常+故障注入）
#   "successful_recoveries": 3       # 成功恢复次数
# }

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from .judge_service import JudgeService
from .schemas import JudgeRequest, JudgeResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="task2 OJ - FastAPI Judge API",
        version="1.0.0",
    )

    task2_root = Path(__file__).resolve().parents[1]
    app.state.judge_service = JudgeService(task2_root=task2_root)

    @app.get("/api/v1/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/api/v1/judge", response_model=JudgeResponse)
    async def judge(req: JudgeRequest) -> JudgeResponse:
        try:
            service: JudgeService = app.state.judge_service
            return await run_in_threadpool(service.judge, req.problem_id, req.code)
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app


app = create_app()

