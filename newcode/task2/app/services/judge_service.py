import os
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Deque, Optional

from app.core.config import load_config
from app.core.oj_engine import OJEngine
from app.core.project_manager import create_user_project
from app.core.qemu_manager import QemuManager
from app.core.ssh_executor import SSHExecutor
from app.models.schemas import JudgeResponse, TestCaseResult


_JUDGE_LOCK = threading.Lock()

_TASK2_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = load_config(str(_TASK2_ROOT / "config.json"))
_QEMU_MGR = QemuManager(_CONFIG["qemu"], container_id=None)


def _to_optional_int_ms(exec_time_ms: Optional[str]) -> Optional[int]:
    if exec_time_ms is None:
        return None
    try:
        # SSHExecutor.execute_timed returns string like "12" (ms)
        return int(exec_time_ms)
    except Exception:
        return None


def judge(problem_id: str, code: str) -> JudgeResponse:
    """
    严格复现原项目 main.py 的 run_judge：
    SSH 连接→上传代码→gcc 编译→正常测试→故障注入→重跑→compare 对比→统计 AC/WA/RE 与 survival_rate
    """
    with _JUDGE_LOCK:
        job_user_id = str(uuid.uuid4())
        job_tmp_dir = _TASK2_ROOT / ".temp" / f"job_{job_user_id}"
        job_tmp_dir.mkdir(parents=True, exist_ok=True)

        log_deque: Deque[str] = deque(maxlen=5000)
        recovery_event = threading.Event()

        def _logger(msg: str) -> None:
            if msg:
                log_deque.append(msg)
                # 与原 GUI check_recovery() 语义保持一致
                if "ERROR_RECOVERED" in msg:
                    recovery_event.set()

        executor = SSHExecutor(_CONFIG["ssh"])
        try:
            # 1) 创建用户工程，并把用户代码写入 main.c
            # core/project_manager.py 依赖相对路径，因此切换到 task2_root
            cwd0 = os.getcwd()
            os.chdir(str(_TASK2_ROOT))
            try:
                user_code_path = create_user_project(problem_id, user_id=job_user_id)
                # create_user_project 返回的是“相对当前工作目录”的路径
                user_code_abs = (
                    Path(user_code_path)
                    if Path(user_code_path).is_absolute()
                    else (_TASK2_ROOT / user_code_path)
                )
                user_code_abs.parent.mkdir(parents=True, exist_ok=True)
            finally:
                os.chdir(cwd0)

            user_code_abs.write_text(code, encoding="utf-8")

            # 2) SSH + QEMU 初始化
            executor.connect()
            _QEMU_MGR.start_qemu(_logger)

            # 3) 上传代码并编译
            executor.upload_file(str(user_code_path), "app.c")

            out, err, _ = executor.execute_timed("gcc app.c -o app 2>&1", timeout=60)
            combined = (out or "") + (err or "")
            if ("error" in (out or "").lower()) or ("error" in (err or "").lower()):
                # 编译失败不参与后续测试
                return JudgeResponse(
                    overall_result="RE",
                    test_cases=[
                        TestCaseResult(
                            name="compile",
                            status="RE",
                            time_ms=None,
                            info=combined[-2000:] if combined else "编译错误",
                        )
                    ],
                    survival_rate=0.0,
                    total_tests=0,
                    successful_recoveries=0,
                )

            # 4) 测试用例遍历（正常 + 故障注入，共 2x）
            problem_dir = _TASK2_ROOT / problem_id
            cases = OJEngine.get_test_cases(str(problem_dir))
            normal_cases = len(cases)

            test_cases: list[TestCaseResult] = []
            successful_recoveries = 0

            local_res_path = job_tmp_dir / "res.out"

            for case in cases:
                name = case["name"]

                # 4.1) 正常测试流程
                try:
                    executor.upload_file(case["in_path"], "in.txt")
                    _, _, exec_time = executor.execute_timed("./app < in.txt > out.txt", timeout=30)
                    executor.download_file("out.txt", str(local_res_path))

                    expected = Path(case["out_path"]).read_text(encoding="utf-8", errors="ignore")
                    actual = local_res_path.read_text(encoding="utf-8", errors="ignore")
                    is_ac = OJEngine.compare(expected, actual)

                    status = "AC" if is_ac else "WA"
                    info = "通过" if is_ac else "答案错误"
                    test_cases.append(
                        TestCaseResult(
                            name=name,
                            status=status,
                            time_ms=_to_optional_int_ms(exec_time),
                            info=info,
                        )
                    )
                except Exception as e:
                    test_cases.append(
                        TestCaseResult(
                            name=name,
                            status="RE",
                            time_ms=None,
                            info=str(e)[:20],
                        )
                    )

                # 4.2) 故障注入测试流程（调用 core/qemu_manager.py.inject_fault）
                try:
                    recovery_event.clear()
                    _QEMU_MGR.inject_fault(fault_type="memory_bitflip")

                    executor.upload_file(case["in_path"], "in.txt")
                    _, _, exec_time = executor.execute_timed("./app < in.txt > out.txt", timeout=30)
                    executor.download_file("out.txt", str(local_res_path))

                    expected = Path(case["out_path"]).read_text(encoding="utf-8", errors="ignore")
                    actual = local_res_path.read_text(encoding="utf-8", errors="ignore")
                    is_ac = OJEngine.compare(expected, actual)

                    recovery_success = recovery_event.is_set()
                    if recovery_success:
                        successful_recoveries += 1

                    if not recovery_success:
                        status = "RE"
                        info = "故障后未恢复"
                    else:
                        status = "AC" if is_ac else "WA"
                        info = "通过" if is_ac else "答案错误"

                    test_cases.append(
                        TestCaseResult(
                            name=name,
                            status=status,
                            time_ms=_to_optional_int_ms(exec_time),
                            info=info,
                        )
                    )
                except Exception as e:
                    test_cases.append(
                        TestCaseResult(
                            name=name,
                            status="RE",
                            time_ms=None,
                            info=str(e)[:20],
                        )
                    )

            total_tests = normal_cases * 2
            survival_rate = (successful_recoveries / total_tests * 100.0) if total_tests else 0.0

            # overall_result：只基于“正常”那一半结果
            normal_statuses = [tc.status for tc in test_cases[:normal_cases]]
            if any(s == "RE" for s in normal_statuses):
                overall_result = "RE"
            elif all(s == "AC" for s in normal_statuses) and normal_cases > 0:
                overall_result = "AC"
            else:
                overall_result = "WA"

            return JudgeResponse(
                overall_result=overall_result,
                test_cases=test_cases,
                survival_rate=survival_rate,
                total_tests=total_tests,
                successful_recoveries=successful_recoveries,
            )
        finally:
            try:
                executor.close()
            except Exception:
                pass

