from fastapi import APIRouter, HTTPException

from app.models.schemas import JudgeRequest, JudgeResponse
from app.services import judge_service


router = APIRouter()


@router.post("/api/v1/judge", response_model=JudgeResponse)
def judge_endpoint(req: JudgeRequest) -> JudgeResponse:
    result = judge_service.judge(req.problem_id, req.code, req.judge_mode)
    if result is None:
        raise HTTPException(status_code=501, detail="judge() 未实现：请填充服务层逻辑")
    return result

