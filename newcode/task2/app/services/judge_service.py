import os
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Deque, Optional
import random

from core.baremetal_builder import BareMetalBuilder
from core.baremetal_code_prep import prepare_baremetal_uart_code
from core.baremetal_uart_runner import BareMetalUartRunner
from app.core.config import load_config
from app.core.oj_engine import OJEngine
from app.core.project_manager import create_user_project
from app.core.qemu_manager import QemuManager
from core.fault_injection_config import load_fault_injection_config, random_sram_flip_address
from core.stack_watermark import testcase_stack_wm_api
from app.core.ssh_executor import SSHExecutor
from app.models.schemas import JudgeResponse, TestCaseResult


_JUDGE_LOCK = threading.Lock()

_TASK2_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = load_config(str(_TASK2_ROOT / "config.json"))
_FAULT_CFG = load_fault_injection_config(_TASK2_ROOT)
_QEMU_MGR = QemuManager(_CONFIG["qemu"], container_id=None, fault_config=_FAULT_CFG)

_BAREMETAL_BUILDER = BareMetalBuilder()
_BAREMETAL_RUNNER = BareMetalUartRunner(_QEMU_MGR)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _judge_baremetal_uart(problem_id: str, code: str) -> JudgeResponse:
    """
    Cortex-M (stm32vldiscovery) bare-metal UART OJ mode.
    生存率定义为注入后 AC / 注入次数（ERROR_RECOVERED 仅观测，不作为强制门槛）。
    """
    job_user_id = str(uuid.uuid4())
    job_tmp_dir = _TASK2_ROOT / ".temp" / f"job_{job_user_id}"
    job_tmp_dir.mkdir(parents=True, exist_ok=True)

    log_deque: Deque[str] = deque(maxlen=5000)
    recovery_event = threading.Event()

    def _logger(msg: str) -> None:
        if msg:
            log_deque.append(msg)
            if "ERROR_RECOVERED" in msg:
                recovery_event.set()

    # 1) local build
    local_main_c = job_tmp_dir / "main.c"
    prepared_code = prepare_baremetal_uart_code(code)
    local_main_c.write_text(prepared_code, encoding="utf-8")

    try:
        artifacts = _BAREMETAL_BUILDER.build(local_main_c, job_tmp_dir / "firmware")
    except Exception as e:
        return JudgeResponse(
            overall_result="RE",
            test_cases=[
                TestCaseResult(
                    name="compile",
                    status="RE",
                    time_ms=None,
                    info=str(e)[:2000],
                )
            ],
            survival_rate=0.0,
            total_tests=0,
            successful_recoveries=0,
        )

    problem_dir = _TASK2_ROOT / problem_id
    cases = OJEngine.get_test_cases(str(problem_dir))
    normal_cases = len(cases)

    test_cases: list[TestCaseResult] = []
    successful_recoveries = 0
    injection_total = 0

    for case in cases:
        name = case["name"]
        in_text = _read_text(Path(case["in_path"]))
        expected = _read_text(Path(case["out_path"]))

        # 2.1) normal
        try:
            normal_res = _BAREMETAL_RUNNER.run_once(
                artifacts.bin_path,
                in_text,
                logger=_logger,
                firmware_elf_path=artifacts.elf_path,
                stack_watermark_cfg=_CONFIG.get("stack_watermark"),
                total_timeout_sec=10.0,
                uart_connect_timeout_sec=5.0,
                uart_output_idle_sec=0.35,
            )
            is_ac = OJEngine.compare(expected, normal_res.actual_output)
            status = "AC" if is_ac else "WA"
            test_cases.append(
                TestCaseResult(
                    name=name,
                    status=status,
                    time_ms=normal_res.exec_time_ms,
                    info="通过" if is_ac else "答案错误",
                    **testcase_stack_wm_api(
                        normal_res.stack_watermark,
                        _CONFIG.get("stack_watermark"),
                        _FAULT_CFG,
                    ),
                )
            )
        except Exception as e:
            test_cases.append(
                TestCaseResult(
                    name=name,
                    status="RE",
                    time_ms=None,
                    info=str(e)[:50],
                )
            )

        # 2.2) fault injection + re-run（GDB 位翻转；生存率分母仅计注入次数）
        injection_total += 1
        try:
            recovery_event.clear()
            addr = random_sram_flip_address(_FAULT_CFG)
            bit = random.randint(0, 31)
            injected_res = _BAREMETAL_RUNNER.run_once(
                artifacts.bin_path,
                in_text,
                logger=_logger,
                firmware_elf_path=artifacts.elf_path,
                stack_watermark_cfg=_CONFIG.get("stack_watermark"),
                inject_error_addr=addr,
                inject_error_bit=bit,
                recovery_event=recovery_event,
                total_timeout_sec=10.0,
                uart_connect_timeout_sec=5.0,
                uart_output_idle_sec=0.35,
            )

            is_ac = OJEngine.compare(expected, injected_res.actual_output)
            # 新定义：注入后 AC 即算恢复（不再强制 ERROR_RECOVERED）
            if is_ac:
                successful_recoveries += 1

            status = "AC" if is_ac else "WA"
            test_cases.append(
                TestCaseResult(
                    name=name,
                    status=status,
                    time_ms=injected_res.exec_time_ms,
                    info="通过" if is_ac else "答案错误",
                    **testcase_stack_wm_api(
                        injected_res.stack_watermark,
                        _CONFIG.get("stack_watermark"),
                        _FAULT_CFG,
                    ),
                )
            )
        except Exception as e:
            test_cases.append(
                TestCaseResult(
                    name=name,
                    status="RE",
                    time_ms=None,
                    info=str(e)[:50],
                )
            )

    survival_rate = (
        (successful_recoveries / injection_total) if injection_total else 0.0
    )
    total_tests = injection_total

    # overall_result：仅基于各用例的「无注入」运行（每个用例 2 条结果中的第 1 条）
    normal_statuses = [test_cases[i].status for i in range(0, len(test_cases), 2)]
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


def _to_optional_int_ms(exec_time_ms: Optional[str]) -> Optional[int]:
    if exec_time_ms is None:
        return None
    try:
        # SSHExecutor.execute_timed returns string like "12" (ms)
        return int(exec_time_ms)
    except Exception:
        return None


def judge(problem_id: str, code: str, judge_mode: str = "c") -> JudgeResponse:
    """
    SSH 连接→上传代码→gcc 编译→正常测试；裸机模式另含 GDB 位翻转注入与生存率。
    普通 C 模式不支持 QEMU 内故障注入，survival_rate 恒为 0、total_tests 为 0。
    """
    with _JUDGE_LOCK:
        if judge_mode == "cortexm_baremetal_uart":
            return _judge_baremetal_uart(problem_id, code)

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

            # 4) 测试用例遍历（普通 C 仅正常跑；故障注入见裸机模式）
            problem_dir = _TASK2_ROOT / problem_id
            cases = OJEngine.get_test_cases(str(problem_dir))
            normal_cases = len(cases)

            test_cases: list[TestCaseResult] = []

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

            total_tests = 0
            survival_rate = 0.0
            successful_recoveries = 0

            # overall_result：仅基于各用例正常跑结果
            normal_statuses = [tc.status for tc in test_cases]
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

