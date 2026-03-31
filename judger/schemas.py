from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class JudgeRequest(BaseModel):
    problem_id: str
    code: str
    judge_mode: Literal["c", "cortexm_baremetal_uart"] = "c"


class TestCaseResult(BaseModel):
    name: str
    status: Literal["AC", "WA", "RE", "TLE", "CE"]
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
    overall_result: Literal["AC", "WA", "RE", "TLE", "CE"]
    test_cases: list[TestCaseResult]
    survival_rate: float = Field(
        description="裸机：注入后 AC 次数/注入总次数，范围[0,1]；普通 C 为 0"
    )
    total_tests: int = Field(description="故障注入试验次数（分母）；普通 C 为 0")
    successful_recoveries: int = Field(description="注入后 AC 的次数（分子）")
    resource_usage_summary: Optional[str] = Field(
        default=None,
        description="裸机：.map 静态 Flash/RAM 占用一行摘要；普通 C 或未解析时为 None",
    )
    resource_usage: Optional[dict[str, Any]] = Field(
        default=None,
        description="裸机：资源占用 JSON（无 per-section 列表）；普通 C 或未解析时为 None",
    )
    final_score: Optional[float] = Field(
        default=None,
        description="裸机综合得分 [0,100] 两位小数；非 cortexm_baremetal_uart 或未计算时为 None",
    )
    final_score_breakdown: Optional[dict[str, Any]] = Field(
        default=None,
        description="裸机分项得分明细；无综合分时为 None",
    )
    clang_tidy_output: Optional[str] = Field(
        default=None,
        description=(
            "clang-tidy stdout+stderr；服务端可截断仅保留 UTF-8 尾部约 32KB，"
            "前缀带 ...[truncated head]...；未执行或无输出时为 None"
        ),
    )

