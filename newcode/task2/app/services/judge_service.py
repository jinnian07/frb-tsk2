"""薄封装：委托 ``judger.JudgeService``。"""

import threading
from pathlib import Path

from judger.judge_service import JudgeService
from judger.schemas import JudgeResponse

_JUDGE_LOCK = threading.Lock()
_TASK2_ROOT = Path(__file__).resolve().parents[2]
_SERVICE = JudgeService(task2_root=_TASK2_ROOT)


def judge(problem_id: str, code: str, judge_mode: str = "c") -> JudgeResponse:
    """
    SSH / 裸机判题；与 api 侧语义一致（由 judger 统一实现）。
    """
    with _JUDGE_LOCK:
        return _SERVICE.judge(problem_id, code, judge_mode)
