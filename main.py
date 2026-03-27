import ctypes
import tkinter as tk
from typing import List, Optional
from tkinter import ttk, messagebox
import threading
import os
import json
import time
import subprocess
import random  # 新增随机数模块
from pathlib import Path

from judger.core.oj_engine import OJEngine
from judger.core.baremetal_builder import BareMetalBuilder
from judger.core.baremetal_code_prep import prepare_baremetal_uart_code
from judger.core.baremetal_uart_runner import BareMetalUartRunner
from judger.core.ssh_executor import SSHExecutor
from judger.core.qemu_manager import QemuManager
from judger.core.config import load_config, scan_problems
from judger.core.fault_injection_config import (
    load_fault_injection_config,
    random_sram_flip_address,
)
from judger.core.map_resource_usage import (
    RESOURCE_USAGE_LOG_PREFIX,
    analyze_map_usage,
    format_resource_usage_summary,
)
from judger.core.stack_watermark import (
    format_stack_watermark_composite_log_line,
    format_watermark_summary,
)

from ui.components import OJComponents
from ui.md_viewer import MDViewer
from judger.core.project_manager import create_user_project
from judger.core.static_analysis import build_clang_tidy_command

class QemuOJApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Embedded OJ System - QEMU v1.0")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)
        
        self.config = load_config()
        self.task2_root = Path(__file__).resolve().parent
        self.fault_config = load_fault_injection_config(self.task2_root)
        self.executor = SSHExecutor(self.config['ssh'])
        self.current_problem = "P0001"
        self.qemu_mgr = None
        self.judge_running = False
        self.survival_injection_total = 0
        self.survival_injection_survived = 0
        self.judge_mode = tk.StringVar(value="cortexm")  # GUI 默认仍可跑普通 C，首次以普通 C 为准
        self.bare_builder = BareMetalBuilder()
        self.bare_runner = None
        
        self.setup_ui()
        self.root.after(100, self.init_qemu)

    def setup_ui(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        ttk.Label(toolbar, text="选择题目:").pack(side=tk.LEFT)
        problems = scan_problems()
        if not problems:
            problems = ["P0001"]
        self.prob_combo = ttk.Combobox(toolbar, values=problems, state="readonly", width=10)
        self.prob_combo.set(problems[0] if problems else "P0001")
        self.prob_combo.pack(side=tk.LEFT, padx=5)
        self.prob_combo.bind("<<ComboboxSelected>>", lambda e: self.load_problem())

        ttk.Separator(toolbar, orient="vertical").pack(side=tk.LEFT, padx=10, fill="y")

        ttk.Label(toolbar, text="评测模式:").pack(side=tk.LEFT, padx=5)
        self.mode_combo = ttk.Combobox(
            toolbar,
            values=["普通 C", "裸机 Cortex-M UART"],
            state="readonly",
            width=18,
        )
        self.mode_combo.set("普通 C")
        self.mode_combo.pack(side=tk.LEFT, padx=5)
        
        self.btn_judge = ttk.Button(toolbar, text="▶ 提交评测", command=self.start_judge_thread)
        self.btn_judge.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = ttk.Button(toolbar, text="⏹ 停止", command=self.stop_judge, state="disabled")
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(toolbar, text="🔄 刷新题目", command=self.refresh_problems).pack(side=tk.LEFT, padx=5)

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        
        self.qemu_container = tk.Frame(left_frame, width=450, bg="black")
        self.qemu_container.pack(fill=tk.BOTH, expand=True)
        
        log_frame, self.log_text = OJComponents.create_log_viewer(left_frame)
        log_frame.pack(fill=tk.X, padx=2, pady=2)

        self.md_view = MDViewer(paned, width=400, height=600)
        paned.add(self.md_view, weight=1)

        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        
        self.res_table = OJComponents.create_result_table(right_frame)
        self.res_table.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.editor = OJComponents.create_editor(right_frame)
        self.editor.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        ttk.Button(toolbar, text="💾 保存代码", command=self.save_code).pack(side=tk.LEFT, padx=5)

        self.root.after(200, self.load_problem)

    def init_qemu(self):
        self.qemu_container.update_idletasks()
        container_id = self.qemu_container.winfo_id()
        self.qemu_mgr = QemuManager(
            self.config["qemu"], container_id, fault_config=self.fault_config
        )
        self.log("系统初始化完成，等待 QEMU 启动...")
        self.qemu_mgr.start_qemu(self.log)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _do_update(self, name, status, time_val=None, stack_col="-", info=None):
        """主线程更新结果表（由 update_case_result 经 root.after 调度）。"""
        time_str = "-" if time_val is None else str(time_val)
        stack_str = "-" if stack_col is None or stack_col == "" else str(stack_col)
        info_str = "-" if info is None else str(info)
        for item in self.res_table.get_children():
            vals = self.res_table.item(item, "values")
            if vals and vals[0] == name:
                self.res_table.item(
                    item, values=(name, status, time_str, stack_str, info_str)
                )
                break

    def refresh_problems(self):
        problems = scan_problems()
        if problems:
            self.prob_combo["values"] = problems

    def load_problem(self):
        try:
            self.current_problem = self.prob_combo.get()
            
            md_path = os.path.join(self.current_problem, "题面.md")
            if os.path.exists(md_path):
                try:
                    self.md_view.display_md(md_path)
                except Exception as e:
                    self.log(f"显示题面时出错: {str(e)}")
                    self.show_fallback_problem_description(md_path)
            else:
                messagebox.showerror("错误", f"题目 {self.current_problem} 缺少题面.md 文件")
                return
            
            test_cases = OJEngine.get_test_cases(self.current_problem)
            self.res_table.delete(*self.res_table.get_children())
            for case in test_cases:
                self.res_table.insert("", "end", values=(case["name"], "待测", "-", "-", "-"))
            
            self.user_code_path = create_user_project(self.current_problem)
            if os.path.exists(self.user_code_path):
                with open(self.user_code_path, "r", encoding="utf-8") as f:
                    code_content = f.read()
                self.editor.delete("1.0", tk.END)
                self.editor.insert("1.0", code_content)
            else:
                messagebox.showerror("错误", f"用户工程创建失败：{self.user_code_path}")
                return

            self.btn_judge.config(state="normal")
            
        except Exception as e:
            self.log(f"加载题目时出错: {str(e)}")
            messagebox.showerror("错误", f"加载题目时出错: {str(e)}")

    def show_fallback_problem_description(self, md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.log("="*50)
            self.log(f"题目: {self.current_problem}")
            self.log("题面内容:")
            self.log(content)
            self.log("="*50)
            messagebox.showinfo("题面加载成功", 
                              f"已加载题目 {self.current_problem} 的题面\n"
                              "详细内容请查看日志区域")
        except Exception as e:
            self.log(f"无法读取题面文件: {str(e)}")
            messagebox.showerror("错误", f"无法读取题面文件: {str(e)}")

    def load_template_code(self):
        template_path = os.path.join(self.current_problem, "template.c")
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                self.editor.delete(1.0, tk.END)
                self.editor.insert(1.0, f.read())

    def save_code(self):
        if hasattr(self, 'user_code_path'):
            code = self.editor.get("1.0", tk.END)
            with open(self.user_code_path, "w", encoding="utf-8") as f:
                f.write(code)
            self.log(f"代码已保存到：{self.user_code_path}")
        else:
            messagebox.showwarning("警告", "请先选择题目！")

    def _flush_log_lines_on_main_thread(self, lines: List[str]) -> None:
        """Worker thread schedules batched log writes; blocks until the main loop runs them."""
        done = threading.Event()

        def _do():
            for line in lines:
                self.log(line)
            done.set()

        self.root.after(0, _do)
        done.wait(timeout=120.0)

    def _perform_static_check_after_judge(self, user_code: str) -> None:
        """
        clang-tidy 在评测工作线程内执行；日志通过主线程写入，整块排在当次评测日志之后。
        空代码：跳过 clang-tidy，仍输出简短说明（评测已在此前完成）。
        合并流程中不弹静态检查相关 messagebox，结论只看日志。
        """
        task2_dir = Path(__file__).resolve().parent
        lines: List[str] = ["======== 静态检查 ========"]

        if not user_code.strip():
            lines.append("静态检查：代码为空，跳过 clang-tidy")
            lines.append("======== 静态检查结束 ========")
            self._flush_log_lines_on_main_thread(lines)
            return

        temp_c_path = task2_dir / "temp_static_check.c"
        try:
            temp_c_path.write_text(user_code, encoding="utf-8")
        except OSError as e:
            lines.append(f"静态检查：无法写入临时文件: {e}")
            lines.append("======== 静态检查结束 ========")
            self._flush_log_lines_on_main_thread(lines)
            return

        lines.append("开始静态检查（嵌入式 C：arm-none-eabi / freestanding / Cortex-M3）...")

        try:
            cmd, notes = build_clang_tidy_command(
                source_file=temp_c_path,
                task2_dir=task2_dir,
            )
            lines.extend(notes)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(task2_dir),
            )
            output = (result.stdout or "") + (result.stderr or "")
            lines.append("静态检查完成")
            lines.append(output.rstrip() if output.strip() else "(clang-tidy 无 stdout/stderr 输出)")
        except FileNotFoundError:
            lines.append(
                "静态检查失败：未找到 clang-tidy，请安装 LLVM 并将 clang-tidy 加入 PATH"
            )
        except subprocess.TimeoutExpired:
            lines.append("静态检查超时（>120s），已终止")
        except Exception as e:
            lines.append(f"静态检查失败：{e}")
        finally:
            try:
                if temp_c_path.is_file():
                    temp_c_path.unlink()
            except OSError:
                pass

        lines.append("======== 静态检查结束 ========")
        self._flush_log_lines_on_main_thread(lines)

    def run_judge(self):
        try:
            self.log("正在连接 SSH...")
            self.executor.connect()
            self.log("SSH 连接成功")
            
            local_code = "temp_code.c"
            with open(local_code, "w", encoding='utf-8') as f:
                f.write(self.editor.get(1.0, tk.END))
            
            self.log("正在上传代码...")
            self.executor.upload_file(local_code, "app.c")
            
            self.log("正在编译...")
            out, err, _ = self.executor.execute_timed("gcc app.c -o app 2>&1")
            if "error" in out.lower() or "error" in err.lower():
                messagebox.showerror("编译错误", out + err)
                self.log(f"编译失败: {err}")
                return
            self.log("编译成功")

            cases = OJEngine.get_test_cases(self.current_problem)
            if not os.path.exists(".temp"):
                os.makedirs(".temp")

            self.log(
                "提示：普通 C（SSH）评测不支持 QEMU 内存位翻转；"
                "异常注入生存率仅在「裸机 Cortex-M UART」模式下统计。"
            )

            ac_count = 0
            for i, case in enumerate(cases):
                if self.executor.should_stop():
                    self.log("评测已取消")
                    self.update_case_result(case['name'], "取消", "-", "-", "用户停止")
                    break
                
                self.update_case_result(case['name'], "运行中", "-", "-", "-")
                self.log(f"测试 {case['name']}...")

                # 正常测试流程
                try:
                    self.executor.upload_file(case['in_path'], "in.txt")
                    _, _, exec_time = self.executor.execute_timed("./app < in.txt > out.txt")
                    
                    local_res = f".temp/res_{i}.out"
                    self.executor.download_file("out.txt", local_res)
                    
                    with open(case['out_path'], 'r', encoding='utf-8') as f:
                        expected = f.read()
                    with open(local_res, 'r', encoding='utf-8') as f:
                        actual = f.read()
                    
                    is_ac = OJEngine.compare(expected, actual)
                    status = "AC" if is_ac else "WA"
                    if is_ac:
                        ac_count += 1
                    
                    self.update_case_result(
                        case['name'],
                        status,
                        exec_time,
                        "-",
                        "通过" if is_ac else "答案错误",
                    )
                    self.log(f"{case['name']}: {status} ({exec_time}ms)")

                except Exception as e:
                    self.update_case_result(case['name'], "RE", "-", "-", str(e)[:20])
                    self.log(f"{case['name']}: 运行错误 - {e}")

            total = len(cases)
            self.log(f"\n=== 最终评测结果 ===")
            self.log(f"正常通过: {ac_count}/{total}")
            self.calculate_survival_rate()

        except Exception as e:
            self.log(f"评测错误: {str(e)}")
            messagebox.showerror("错误", str(e))

    def run_judge_baremetal_uart(self):
        """
        Cortex-M bare-metal UART OJ mode (QEMU stm32vldiscovery + USART1).
        - 每个测试点一次性启动 QEMU，喂入 UART 输入，捕获 UART 输出后终止 QEMU
        - 故障注入：UART 连通后由 arm-none-eabi-gdb 经 gdbstub 对 SRAM 做位翻转，再喂入同一组输入
        - 恢复判定：注入后 AC 即算恢复；ERROR_RECOVERED 日志仅用于调试观测
        """
        try:
            self.log("开始裸机 Cortex-M UART 评测...")

            # Ensure QEMU manager exists (init_qemu creates it once).
            if self.qemu_mgr is None:
                raise RuntimeError("QEMU 管理器尚未初始化")

            bare_runner = BareMetalUartRunner(self.qemu_mgr)

            job_tmp_dir = os.path.join(".temp", f"gui_job_{int(time.time())}")
            os.makedirs(job_tmp_dir, exist_ok=True)

            local_main_c = os.path.join(job_tmp_dir, "main.c")
            with open(local_main_c, "w", encoding="utf-8") as f:
                user_code = self.editor.get(1.0, tk.END)
                prepared_code = prepare_baremetal_uart_code(user_code)
                f.write(prepared_code)

            self.log("交叉编译固件中...")
            artifacts = self.bare_builder.build(Path(local_main_c), Path(job_tmp_dir) / "firmware")

            resource_usage_log_line: Optional[str] = None
            try:
                mp = artifacts.map_path
                if mp is not None and mp.is_file():
                    report = analyze_map_usage(
                        mp,
                        linker_script=self.bare_builder.linker_script,
                    )
                    report = dict(report)
                    report["sections"] = []
                    resource_usage_log_line = (
                        f"{RESOURCE_USAGE_LOG_PREFIX}"
                        f"{format_resource_usage_summary(report)}"
                    )
                else:
                    resource_usage_log_line = (
                        f"{RESOURCE_USAGE_LOG_PREFIX}未找到 firmware.map，跳过解析"
                    )
            except Exception as e:
                resource_usage_log_line = (
                    f"{RESOURCE_USAGE_LOG_PREFIX}解析失败（{e!s}）"
                )

            self.log(
                resource_usage_log_line
                or f"{RESOURCE_USAGE_LOG_PREFIX}资源占用未解析"
            )

            cases = OJEngine.get_test_cases(self.current_problem)
            ac_count = 0
            normal_stack_scores: list[Optional[int]] = []

            for i, case in enumerate(cases):
                if not self.judge_running:
                    self.log("评测已取消")
                    self.update_case_result(case["name"], "取消", "-", "-", "用户停止")
                    break

                self.update_case_result(case["name"], "运行中", "-", "-", "-")
                self.log(f"测试 {case['name']} (正常)...")

                in_text = open(case["in_path"], "r", encoding="utf-8", errors="ignore").read()
                expected = open(case["out_path"], "r", encoding="utf-8", errors="ignore").read()

                # Normal run
                recovery_event = threading.Event()

                def _logger(msg: str):
                    if msg:
                        self.log(msg)
                        if "ERROR_RECOVERED" in msg:
                            recovery_event.set()

                normal_res = bare_runner.run_once(
                    artifacts.bin_path,
                    in_text,
                    logger=_logger,
                    firmware_elf_path=artifacts.elf_path,
                    stack_watermark_cfg=self.config.get("stack_watermark"),
                    total_timeout_sec=10.0,
                    uart_connect_timeout_sec=5.0,
                    uart_output_idle_sec=0.35,
                )

                is_ac = OJEngine.compare(expected, normal_res.actual_output)
                status = "AC" if is_ac else "WA"
                if is_ac:
                    ac_count += 1
                _sw = normal_res.stack_watermark
                _stack_txt = (
                    format_watermark_summary(_sw) if _sw else "-"
                ) or "-"
                self.update_case_result(
                    case["name"],
                    status,
                    normal_res.exec_time_ms,
                    _stack_txt,
                    "通过" if is_ac else "答案错误",
                )
                self.log(f"{case['name']}: {status} ({normal_res.exec_time_ms}ms)")
                _sc = (
                    _sw.get("stack_watermark_score") if _sw else None
                )
                normal_stack_scores.append(_sc)

                # Fault injection run
                self.log("\n--- 注入故障后重新测试 ---")
                recovery_event.clear()

                addr = random_sram_flip_address(self.fault_config)
                bit = random.randint(0, 31)

                injected_res = bare_runner.run_once(
                    artifacts.bin_path,
                    in_text,
                    logger=_logger,
                    firmware_elf_path=artifacts.elf_path,
                    stack_watermark_cfg=self.config.get("stack_watermark"),
                    inject_error_addr=addr,
                    inject_error_bit=bit,
                    recovery_event=recovery_event,
                    total_timeout_sec=10.0,
                    uart_connect_timeout_sec=5.0,
                    uart_output_idle_sec=0.35,
                )

                self.survival_injection_total += 1
                is_ac2 = OJEngine.compare(expected, injected_res.actual_output)
                if is_ac2:
                    self.survival_injection_survived += 1
                status2 = "AC" if is_ac2 else "WA"
                _sw2 = injected_res.stack_watermark
                _stack_txt2 = (
                    format_watermark_summary(_sw2) if _sw2 else "-"
                ) or "-"
                self.update_case_result(
                    case["name"],
                    status2,
                    injected_res.exec_time_ms,
                    _stack_txt2,
                    "通过" if is_ac2 else "答案错误",
                )
                self.log(
                    f"{case['name']}: {status2} ({injected_res.exec_time_ms}ms) - "
                    f"{'恢复成功（按注入后AC判定）' if is_ac2 else '未恢复（按注入后AC判定）'}"
                )

            total = len(cases)
            self.log("\n=== 最终评测结果 ===")
            self.log(f"正常通过: {ac_count}/{len(cases)}")
            self.calculate_survival_rate()
            self.log(format_stack_watermark_composite_log_line(normal_stack_scores))
            self.log(
                resource_usage_log_line
                or f"{RESOURCE_USAGE_LOG_PREFIX}资源占用未解析"
            )

            if self.config.get("enable_coverage_embedded", False):
                self._run_embedded_coverage_host(
                    prepared_code=prepared_code,
                    cases=cases,
                )

        except Exception as e:
            self.log(f"裸机评测错误: {str(e)}")
            messagebox.showerror("错误", str(e))

    def _run_embedded_coverage_host(self, prepared_code: str, cases: list) -> None:
        """课堂级 gcov：宿主 gcc 近似（不参与 AC/WA）。仅在裸机评测成功后调用。"""
        task2_dir = Path(__file__).resolve().parent
        in_paths = [(task2_dir / c["in_path"]).resolve() for c in cases]
        try:
            from judger.core.coverage_embedded import run_embedded_host_coverage

            res = run_embedded_host_coverage(
                prepared_user_c=prepared_code,
                task2_root=task2_dir,
                problem_id=self.current_problem,
                case_in_paths=in_paths,
                log=self.log,
            )
            if not res.get("summary_text"):
                return
        except Exception as e:
            self.log(f"宿主覆盖率失败: {e}")

    def update_case_result(self, name, status, time_val=None, stack_col="-", info=None):
        self.root.after(
            0,
            lambda: self._do_update(name, status, time_val, stack_col, info),
        )

    def calculate_survival_rate(self):
        if self.survival_injection_total <= 0:
            self.log(
                "异常注入生存率: N/A（本次未执行 GDB 位翻转注入；请使用裸机 Cortex-M UART 模式）"
            )
            return
        rate = self.survival_injection_survived / self.survival_injection_total
        self.log(
            f"异常注入生存率: {rate * 100:.2f}% "
            f"（{self.survival_injection_survived}/{self.survival_injection_total}，"
            f"注入后 AC / 注入总次数）"
        )

    def _judge_worker(self, baremetal_uart: bool) -> None:
        user_code = self.editor.get("1.0", tk.END)
        try:
            if baremetal_uart:
                self.run_judge_baremetal_uart()
            else:
                self.run_judge()
        finally:
            try:
                self._perform_static_check_after_judge(user_code)
            finally:
                self.judge_running = False
                self.root.after(0, lambda: self.btn_judge.config(state="normal"))
                self.root.after(0, lambda: self.btn_stop.config(state="disabled"))

    def start_judge_thread(self):
        if self.judge_running:
            messagebox.showwarning("提示", "评测进行中...")
            return
        self.judge_running = True
        self.survival_injection_total = 0
        self.survival_injection_survived = 0
        self.btn_judge.config(state="disabled")
        self.btn_stop.config(state="normal")
        mode = self.mode_combo.get() if hasattr(self, "mode_combo") else "普通 C"
        bare = "裸机" in mode
        threading.Thread(target=self._judge_worker, args=(bare,), daemon=True).start()

    def stop_judge(self):
        if self.executor:
            self.executor.stop()
            self.log("正在停止评测...")
        if self.qemu_mgr:
            try:
                self.qemu_mgr.stop_qemu()
            except Exception:
                pass
        self.judge_running = False
        self.btn_stop.config(state="disabled")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = QemuOJApp()
    app.run()