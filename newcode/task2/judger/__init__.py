"""评测机库（无 HTTP）：从 task2 根目录运行 ``python main.py`` 时可直接 ``import judger``。"""

from judger.judge_service import JudgeService
from judger.schemas import JudgeRequest, JudgeResponse, TestCaseResult

__all__ = ["JudgeService", "JudgeRequest", "JudgeResponse", "TestCaseResult"]
