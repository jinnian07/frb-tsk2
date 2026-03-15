from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.judge_router import router as judge_router


def create_app() -> FastAPI:
    app = FastAPI(title="task2 OJ - FastAPI Skeleton", version="0.1.0")

    static_dir = Path(__file__).resolve().parent / "static"
    # 静态资源挂载（如未来需要题面、前端等）
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # API 路由
    app.include_router(judge_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    print("Swagger UI: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)

