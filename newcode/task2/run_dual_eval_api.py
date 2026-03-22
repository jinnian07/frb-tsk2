"""
启动双环境评测 FastAPI 服务（工作目录应为 task2 根目录）:
  python run_dual_eval_api.py
默认端口 8090。若提示端口占用，可先关掉旧终端里的服务，或换端口：

  set DUAL_EVAL_PORT=8091
  python run_dual_eval_api.py
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("DUAL_EVAL_PORT", "8090"))
    uvicorn.run(
        "dual_eval.backend.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
