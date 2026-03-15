from typing import Literal, Optional

from pydantic import BaseModel


class JudgeRequest(BaseModel):
    problem_id: str
    code: str


class TestCaseResult(BaseModel):
    name: str
    status: Literal["AC", "WA", "RE", "TLE"]
    time_ms: Optional[int] = None
    info: Optional[str] = None


class JudgeResponse(BaseModel):
    overall_result: Literal["AC", "WA", "RE", "TLE"]
    test_cases: list[TestCaseResult]
    survival_rate: float
    total_tests: int
    successful_recoveries: int

