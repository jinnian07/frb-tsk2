import random
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Optional, Tuple

from core.config import load_config
from core.oj_engine import OJEngine
from core.qemu_manager import QemuManager
from core.ssh_executor import SSHExecutor

from .schemas import (
    JudgeResponse,
    TestCaseResult,
)


class JudgeService:
    """
    复用现有核心判题链路：
    SSH 侧上传代码/输入 -> gcc 编译 -> 运行程序并下载 out.txt -> compare 标准输出
    故障注入侧：下发 inject_error -> 再跑一次 -> 通过 ERROR_RECOVERED 判断恢复
    """

    def __init__(self, task2_root: Path):
        self.task2_root = task2_root
        self.config = load_config(str(self.task2_root / "config.json"))
        self.executor = SSHExecutor(self.config["ssh"])
        # API 场景无 Tk container，故障注入/恢复检测仍可工作（不嵌入窗口）
        self.qemu_mgr = QemuManager(self.config["qemu"], container_id=None)

        # 强制串行：避免多个请求同时改 remote_work_dir / 同一台 QEMU 状态
        self._judge_lock = threading.Lock()

    def _make_logger(self, log_deque: Deque[str], recovery_event: threading.Event) -> Callable[[str], None]:
        def _log(msg: str):
            if msg:
                log_deque.append(msg)
                if "ERROR_RECOVERED" in msg:
                    recovery_event.set()

        return _log

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def inject_fault_memory_bitflip(self):
        address = random.randint(0x20000000, 0x20010000)
        bit = random.randint(0, 31)
        self.qemu_mgr.send_debug_command(f"inject_error {address} {bit}")

    def _to_int_ms(self, exec_time_ms: Optional[str]) -> Optional[int]:
        if exec_time_ms is None:
            return None
        try:
            return int(float(exec_time_ms))
        except Exception:
            return None

    def _classify_run_exception(self, e: Exception) -> Tuple[str, str]:
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            return "TLE", "超时"
        return "RE", str(e)[:50]

    def _connect_with_retry(self, connect_timeout_sec: int = 60):
        deadline = time.time() + connect_timeout_sec
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                self.executor.connect()
                return
            except Exception as e:
                last_err = e
                time.sleep(2)
        raise RuntimeError(f"SSH 连接失败：{last_err}")

    def judge(self, problem_id: str, code: str) -> JudgeResponse:
        with self._judge_lock:
            job_id = str(uuid.uuid4())
            problem_dir = self.task2_root / problem_id
            if not problem_dir.exists():
                raise FileNotFoundError(f"题目目录不存在：{problem_id}")

            # 用于区分本次请求的本地临时文件
            job_tmp_dir = self.task2_root / ".temp" / f"job_{job_id}"
            job_tmp_dir.mkdir(parents=True, exist_ok=True)

            log_deque: Deque[str] = deque(maxlen=5000)
            recovery_event = threading.Event()
            logger = self._make_logger(log_deque, recovery_event)

            # 确保 QEMU 已启动且当前回调已注册
            self.qemu_mgr.start_qemu(logger)

            # 等 QEMU 暖机：不直接睡死，交给 SSH 重试
            self._connect_with_retry(connect_timeout_sec=90)

            test_cases: list[TestCaseResult] = []
            successful_recoveries = 0

            try:
                # 1) 上传代码 + 编译
                local_code = job_tmp_dir / "temp_code.c"
                local_code.write_text(code, encoding="utf-8")

                self.executor.upload_file(str(local_code), "app.c")

                out, err, _ = self.executor.execute_timed("gcc app.c -o app 2>&1", timeout=60)
                combined = (out or "") + (err or "")
                if "error" in combined.lower():
                    return JudgeResponse(
                        overall_result="RE",
                        test_cases=[
                            TestCaseResult(
                                name="compile",
                                status="RE",
                                time_ms=None,
                                info=combined[-2000:],
                            )
                        ],
                        survival_rate=0.0,
                        total_tests=1,
                        successful_recoveries=0,
                    )

                # 2) 正常 + 故障注入双测试流程
                cases = OJEngine.get_test_cases(str(problem_dir))
                total_cases = len(cases)

                for i, case in enumerate(cases):
                    name = case["name"]
                    local_res = job_tmp_dir / f"res_{i}.out"

                    # 2.1) 正常测试流程
                    try:
                        self.executor.upload_file(case["in_path"], "in.txt")
                        _, _, exec_time = self.executor.execute_timed(
                            "./app < in.txt > out.txt", timeout=30
                        )

                        self.executor.download_file("out.txt", str(local_res))

                        expected = self._read_text(Path(case["out_path"]))
                        actual = self._read_text(local_res)

                        is_ac = OJEngine.compare(expected, actual)
                        status = "AC" if is_ac else "WA"

                        test_cases.append(
                            TestCaseResult(
                                name=name,
                                status=status,
                                time_ms=self._to_int_ms(exec_time),
                                info="通过" if is_ac else "答案错误",
                            )
                        )
                    except Exception as e:
                        run_status, info = self._classify_run_exception(e)
                        test_cases.append(
                            TestCaseResult(
                                name=name,
                                status=run_status,
                                time_ms=None,
                                info=info,
                            )
                        )

                    # 2.2) 故障注入测试流程（在 QEMU 中随机注入 bitflip）
                    try:
                        recovery_event.clear()
                        self.inject_fault_memory_bitflip()

                        self.executor.upload_file(case["in_path"], "in.txt")
                        _, _, exec_time = self.executor.execute_timed(
                            "./app < in.txt > out.txt", timeout=30
                        )

                        self.executor.download_file("out.txt", str(local_res))

                        expected = self._read_text(Path(case["out_path"]))
                        actual = self._read_text(local_res)
                        is_ac = OJEngine.compare(expected, actual)

                        recovery_success = recovery_event.is_set()
                        if recovery_success:
                            successful_recoveries += 1

                        if not recovery_success:
                            test_cases.append(
                                TestCaseResult(
                                    name=name,
                                    status="RE",
                                    time_ms=self._to_int_ms(exec_time),
                                    info="故障后未恢复",
                                )
                            )
                        else:
                            status = "AC" if is_ac else "WA"
                            test_cases.append(
                                TestCaseResult(
                                    name=name,
                                    status=status,
                                    time_ms=self._to_int_ms(exec_time),
                                    info="通过" if is_ac else "答案错误",
                                )
                            )
                    except Exception as e:
                        run_status, info = self._classify_run_exception(e)
                        test_cases.append(
                            TestCaseResult(
                                name=name,
                                status=run_status,
                                time_ms=None,
                                info=info,
                            )
                        )

                total_tests = len(test_cases)
                survival_rate = (successful_recoveries / total_tests * 100.0) if total_tests else 0.0

                # overall_result 优先级：TLE > RE > WA > AC
                statuses = {tc.status for tc in test_cases}
                if "TLE" in statuses:
                    overall_result = "TLE"
                elif "RE" in statuses:
                    overall_result = "RE"
                elif all(tc.status == "AC" for tc in test_cases):
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
                    self.executor.close()
                except Exception:
                    pass

