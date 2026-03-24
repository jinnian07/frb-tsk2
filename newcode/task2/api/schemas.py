"""与 judger 共用 Pydantic 模型，避免重复定义。"""

from judger.schemas import JudgeRequest, JudgeResponse, TestCaseResult

__all__ = ["JudgeRequest", "JudgeResponse", "TestCaseResult"]
