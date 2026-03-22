from typing import Literal, Optional

from pydantic import BaseModel, Field


class JudgeRequest(BaseModel):
    problem_id: str
    code: str
    judge_mode: Literal["c", "cortexm_baremetal_uart"] = "c"


class TestCaseResult(BaseModel):
    name: str
    status: Literal["AC", "WA", "RE", "TLE"]
    time_ms: Optional[int] = None
    info: Optional[str] = None
    # 裸机栈染色 + GDB dump 水位线（普通 C 模式为 None）
    stack_watermark_summary: Optional[str] = None
    stack_depth_bytes: Optional[int] = None
    stack_avail_bytes: Optional[int] = None
    stack_min_sp_estimate: Optional[int] = None
    stack_risk: Optional[Literal["low", "medium", "high"]] = None
    stack_watermark_score: Optional[int] = None
    stack_watermark_tier: Optional[str] = None


class JudgeResponse(BaseModel):
    overall_result: Literal["AC", "WA", "RE", "TLE"]
    test_cases: list[TestCaseResult]
    survival_rate: float = Field(
        description="裸机：注入后 AC 次数/注入总次数，范围[0,1]；普通 C 为 0"
    )
    total_tests: int = Field(description="故障注入试验次数（分母）；普通 C 为 0")
    successful_recoveries: int = Field(description="注入后 AC 的次数（分子）")

