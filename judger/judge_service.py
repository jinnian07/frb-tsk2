import logging
import random
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Optional, Tuple

from judger.core.baremetal_builder import BareMetalBuilder
from judger.core.baremetal_code_prep import prepare_baremetal_uart_code
from judger.core.baremetal_uart_runner import BareMetalUartRunner
from judger.core.config import load_config
from judger.core.oj_engine import OJEngine
from judger.core.qemu_manager import QemuManager
from judger.core.fault_injection_config import load_fault_injection_config, random_sram_flip_address
from judger.core.ssh_executor import SSHExecutor
from judger.core.map_resource_usage import (
    RESOURCE_USAGE_LOG_PREFIX,
    analyze_map_usage,
    format_resource_usage_summary,
)
from judger.core.stack_watermark import testcase_stack_wm_api
from judger.core.final_score import (
    clang_tidy_run,
    compute_baremetal_final_score,
    format_final_score_log_lines,
)
from judger.core.coverage_embedded import run_embedded_host_coverage

from judger.schemas import (
    JudgeResponse,
    TestCaseResult,
)

_LOG = logging.getLogger(__name__)


class JudgeService:
    """
    复用现有核心判题链路：
    SSH 侧上传代码/输入 -> gcc 编译 -> 运行程序并下载 out.txt -> compare 标准输出
    裸机侧：GDB + gdbstub 位翻转；生存率定义为注入后 AC / 注入次数（ERROR_RECOVERED 仅观测）
    """

    def __init__(self, task2_root: Path):
        self.task2_root = task2_root
        self.config = load_config(str(self.task2_root / "config.json"))
        self.fault_config = load_fault_injection_config(self.task2_root)
        self.executor = SSHExecutor(self.config["ssh"])
        self.qemu_mgr = QemuManager(
            self.config["qemu"], container_id=None, fault_config=self.fault_config
        )
        self._bare_builder = BareMetalBuilder(
            runtime_dir=self.task2_root / "baremetal",
        )
        self._bare_runner = BareMetalUartRunner(self.qemu_mgr)

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

    def _judge_baremetal_uart(self, problem_id: str, code: str) -> JudgeResponse:
        """
        Cortex-M (stm32vldiscovery) bare-metal UART OJ mode.

        - Build: cross-compile user `code` into `firmware.bin` (QEMU loads via -kernel)
        - Run: one-shot QEMU per test point, feed UART input, capture UART output
        - Fault injection: GDB gdbstub memory bit-flip before UART input
        - Recovery: survival counts AC on injected run (ERROR_RECOVERED is observational)
        """
        job_id = str(uuid.uuid4())
        problem_dir = self.task2_root / problem_id
        if not problem_dir.exists():
            raise FileNotFoundError(f"题目目录不存在：{problem_id}")

        job_tmp_dir = self.task2_root / ".temp" / f"job_{job_id}"
        job_tmp_dir.mkdir(parents=True, exist_ok=True)

        log_deque: Deque[str] = deque(maxlen=5000)
        recovery_event = threading.Event()
        logger = self._make_logger(log_deque, recovery_event)

        try:
            # 1) Local build (no SSH/QEMU guest OS needed).
            local_main_c = job_tmp_dir / "main.c"
            prepared_code = prepare_baremetal_uart_code(code)
            local_main_c.write_text(prepared_code, encoding="utf-8")

            try:
                artifacts = self._bare_builder.build(local_main_c, job_tmp_dir / "firmware")
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

            resource_usage: Optional[dict[str, Any]] = None
            resource_usage_summary: Optional[str] = None
            resource_usage_skip: Optional[str] = None
            try:
                mp = artifacts.map_path
                if mp is None or not mp.is_file():
                    resource_usage_skip = "未找到 firmware.map，跳过解析"
                else:
                    report = analyze_map_usage(
                        mp,
                        linker_script=self._bare_builder.linker_script,
                    )
                    report = dict(report)
                    report["sections"] = []
                    resource_usage = report
                    resource_usage_summary = format_resource_usage_summary(report)
            except Exception as e:
                resource_usage_skip = f"解析失败：{e!s}"[:500]

            if resource_usage_summary:
                _LOG.info("%s%s", RESOURCE_USAGE_LOG_PREFIX, resource_usage_summary)
                if resource_usage:
                    flash_s = resource_usage["sections_summary_bytes"]["flash"]
                    ram_s = resource_usage["sections_summary_bytes"]["ram"]
                    lim = resource_usage["limits"]
                    _LOG.debug(
                        "resource_usage detail: limits.source=%s "
                        "flash_used=%s flash_limit=%s ram_used=%s ram_limit=%s",
                        lim.get("source"),
                        flash_s.get("total_used"),
                        lim.get("flash_bytes"),
                        ram_s.get("total_used"),
                        lim.get("ram_bytes"),
                    )
            elif resource_usage_skip:
                _LOG.info("%s%s", RESOURCE_USAGE_LOG_PREFIX, resource_usage_skip)

            cases = OJEngine.get_test_cases(str(problem_dir))
            test_cases: list[TestCaseResult] = []
            normal_stack_scores: list[Optional[int]] = []
            successful_recoveries = 0
            injection_total = 0

            for case in cases:
                name = case["name"]
                in_text = self._read_text(Path(case["in_path"]))
                expected = self._read_text(Path(case["out_path"]))

                # 2.1) Normal run
                try:
                    normal_res = self._bare_runner.run_once(
                        artifacts.bin_path,
                        in_text,
                        logger=logger,
                        firmware_elf_path=artifacts.elf_path,
                        stack_watermark_cfg=self.config.get("stack_watermark"),
                        total_timeout_sec=10.0,
                        uart_connect_timeout_sec=5.0,
                        uart_output_idle_sec=0.35,
                    )
                    is_ac = OJEngine.compare(expected, normal_res.actual_output)
                    status = "AC" if is_ac else "WA"
                    wm_fields = testcase_stack_wm_api(
                        normal_res.stack_watermark,
                        self.config.get("stack_watermark"),
                        self.fault_config,
                    )
                    test_cases.append(
                        TestCaseResult(
                            name=name,
                            status=status,
                            time_ms=normal_res.exec_time_ms,
                            info="通过" if is_ac else "答案错误",
                            **wm_fields,
                        )
                    )
                    normal_stack_scores.append(wm_fields.get("stack_watermark_score"))
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
                    normal_stack_scores.append(None)
                    continue

                # 2.2) Fault injection + re-run（GDB）
                injection_total += 1
                try:
                    recovery_event.clear()
                    addr = random_sram_flip_address(self.fault_config)
                    bit = random.randint(0, 31)

                    injected_res = self._bare_runner.run_once(
                        artifacts.bin_path,
                        in_text,
                        logger=logger,
                        firmware_elf_path=artifacts.elf_path,
                        stack_watermark_cfg=self.config.get("stack_watermark"),
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
                                self.config.get("stack_watermark"),
                                self.fault_config,
                            ),
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

            total_tests = injection_total
            survival_rate = (
                (successful_recoveries / injection_total) if injection_total else 0.0
            )

            # overall_result 优先级：TLE > RE > WA > AC（仅看无注入运行）
            normal_statuses = [test_cases[i].status for i in range(0, len(test_cases), 2)]
            statuses = set(normal_statuses)
            if "TLE" in statuses:
                overall_result = "TLE"
            elif "RE" in statuses:
                overall_result = "RE"
            elif all(s == "AC" for s in normal_statuses) and normal_statuses:
                overall_result = "AC"
            else:
                overall_result = "WA"

            line_pct: Optional[float] = None
            branch_pct: Optional[float] = None
            if self.config.get("enable_coverage_embedded", False):
                try:
                    in_paths = [Path(c["in_path"]).resolve() for c in cases]
                    cov_res = run_embedded_host_coverage(
                        prepared_user_c=prepared_code,
                        task2_root=self.task2_root,
                        problem_id=problem_id,
                        case_in_paths=in_paths,
                        log=logger,
                    )
                    det = cov_res.get("detail") or {}
                    line_pct = det.get("line_pct")
                    branch_pct = det.get("branch_pct")
                except Exception:
                    pass

            st_elig, st_out = clang_tidy_run(self.task2_root, code)
            _, breakdown = compute_baremetal_final_score(
                static_eligible=st_elig,
                static_clang_output=st_out,
                line_pct=line_pct,
                branch_pct=branch_pct,
                survival_rate=survival_rate,
                injection_total=total_tests,
                normal_stack_scores=normal_stack_scores,
                map_report=resource_usage,
            )
            for ln in format_final_score_log_lines(breakdown):
                _LOG.info("%s", ln)
            _LOG.info("最终得分==%.2f", breakdown["total"])

            return JudgeResponse(
                overall_result=overall_result,
                test_cases=test_cases,
                survival_rate=survival_rate,
                total_tests=total_tests,
                successful_recoveries=successful_recoveries,
                resource_usage_summary=resource_usage_summary,
                resource_usage=resource_usage,
                final_score=breakdown["total"],
                final_score_breakdown=breakdown,
            )
        finally:
            try:
                self.executor.close()
            except Exception:
                pass

    def judge(self, problem_id: str, code: str, judge_mode: str = "c") -> JudgeResponse:
        with self._judge_lock:
            if judge_mode == "cortexm_baremetal_uart":
                return self._judge_baremetal_uart(problem_id, code)

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
                        total_tests=0,
                        successful_recoveries=0,
                    )

                # 2) 普通 C 仅正常跑（GDB 位翻转仅裸机模式）
                cases = OJEngine.get_test_cases(str(problem_dir))

                for i, case in enumerate(cases):
                    name = case["name"]
                    local_res = job_tmp_dir / f"res_{i}.out"

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

                total_tests = 0
                survival_rate = 0.0
                successful_recoveries = 0

                # overall_result 优先级：TLE > RE > WA > AC
                statuses = {tc.status for tc in test_cases}
                if "TLE" in statuses:
                    overall_result = "TLE"
                elif "RE" in statuses:
                    overall_result = "RE"
                elif all(tc.status == "AC" for tc in test_cases) and test_cases:
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
