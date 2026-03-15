import ctypes
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import json
import time
import subprocess
import random  # 新增随机数模块

from core.oj_engine import OJEngine
from core.ssh_executor import SSHExecutor
from core.qemu_manager import QemuManager
from core.config import load_config, scan_problems
from ui.components import OJComponents
from ui.md_viewer import MDViewer
from core.project_manager import create_user_project 

class QemuOJApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Embedded OJ System - QEMU v1.0")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)
        
        self.config = load_config()
        self.executor = SSHExecutor(self.config['ssh'])
        self.current_problem = "P0001"
        self.qemu_mgr = None
        self.judge_running = False
        self.total_tests = 0  # 总测试用例数（含故障注入）
        self.successful_tests = 0  # 成功恢复的测试用例数
        
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
        ttk.Button(toolbar, text="🔍 静态检查", command=self.start_static_check_thread).pack(side=tk.LEFT, padx=5)

        self.root.after(200, self.load_problem)

    def init_qemu(self):
        self.qemu_container.update_idletasks()
        container_id = self.qemu_container.winfo_id()
        self.qemu_mgr = QemuManager(self.config['qemu'], container_id)
        self.log("系统初始化完成，等待 QEMU 启动...")
        self.qemu_mgr.start_qemu(self.log)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def refresh_problems(self):
        problems = scan_problems()
        if problems:
            self.prob_combo['values'] = problems
            self.log(f"已扫描到 {len(problems)} 个题目")
        else:
            self.log("未找到任何题目目录")

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
                self.res_table.insert("", "end", values=(case["name"], "待测", "-", "-"))
            
            self.user_code_path = create_user_project(self.current_problem)
            if os.path.exists(self.user_code_path):
                with open(self.user_code_path, "r", encoding="utf-8") as f:
                    code_content = f.read()
                self.editor.delete("1.0", tk.END)
                self.editor.insert("1.0", code_content)
            else:
                messagebox.showerror("错误", f"用户工程创建失败：{self.user_code_path}")
                return
            
            self.log(f"已加载题目：{self.current_problem}")
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

    def start_static_check_thread(self):
        if self.judge_running:
            messagebox.showwarning("提示", "评测进行中，请结束后重试")
            return
        threading.Thread(target=self.run_static_check, daemon=True).start()

    def run_static_check(self):
        user_code = self.editor.get("1.0", tk.END)
        if not user_code.strip():
            self.log("静态检查：代码为空，跳过")
            return

        temp_c_path = "temp_static_check.c"
        with open(temp_c_path, "w", encoding="utf-8") as f:
            f.write(user_code)

        try:
            self.log("开始静态检查...")
            cmd = [
                "clang-tidy", temp_c_path, "--", "-std=c11", 
                "-target", "x86_64-pc-windows-msvc"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            self.log("静态检查完成")
            self.log(output)
            if "error:" in output or "warning:" in output:
                messagebox.showinfo("静态检查结果", "发现潜在问题，详见日志")
            else:
                messagebox.showinfo("静态检查结果", "✅ 未发现明显问题")
        except Exception as e:
            self.log(f"静态检查失败：{str(e)}")
            messagebox.showerror("错误", f"静态检查失败：{str(e)}")
        finally:
            if os.path.exists(temp_c_path):
                os.remove(temp_c_path)

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
            
            ac_count = 0
            for i, case in enumerate(cases):
                if self.executor.should_stop():
                    self.log("评测已取消")
                    self.update_case_result(case['name'], "取消", "-", "用户停止")
                    break
                
                self.update_case_result(case['name'], "运行中", "-", "-")
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
                    
                    self.update_case_result(case['name'], status, exec_time, 
                                           "通过" if is_ac else "答案错误")
                    self.log(f"{case['name']}: {status} ({exec_time}ms)")

                except Exception as e:
                    self.update_case_result(case['name'], "RE", "-", str(e)[:20])
                    self.log(f"{case['name']}: 运行错误 - {e}")

                # 故障注入测试流程
                self.log("\n--- 注入故障后重新测试 ---")
                try:
                    self.inject_fault(fault_type="memory_bitflip")
                    self.executor.upload_file(case['in_path'], "in.txt")
                    _, _, exec_time = self.executor.execute_timed("./app < in.txt > out.txt")
                    
                    self.executor.download_file("out.txt", local_res)
                    
                    with open(case['out_path'], 'r', encoding='utf-8') as f:
                        expected = f.read()
                    with open(local_res, 'r', encoding='utf-8') as f:
                        actual = f.read()
                    
                    is_ac = OJEngine.compare(expected, actual)
                    status = "AC" if is_ac else "WA"
                    if is_ac:
                        ac_count += 1
                    
                    recovery_status = "成功" if self.check_recovery() else "失败"
                    self.update_case_result(
                        case['name'], 
                        f"RE（故障后{recovery_status}）", 
                        exec_time, 
                        "答案错误" if not is_ac else ""
                    )
                    self.log(f"{case['name']}: {status} ({exec_time}ms) - {recovery_status}")

                except Exception as e:
                    self.update_case_result(case['name'], "RE", "-", str(e)[:20])
                    self.log(f"{case['name']}: 故障后运行错误 - {e}")

            total = len(cases)
            self.log(f"\n=== 最终评测结果 ===")
            self.log(f"正常通过: {ac_count}/{total}")
            self.calculate_survival_rate()

        except Exception as e:
            self.log(f"评测错误: {str(e)}")
            messagebox.showerror("错误", str(e))
        finally:
            self.judge_running = False
            self.root.after(0, lambda: self.btn_judge.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop.config(state="disabled"))

    def inject_fault(self, fault_type="memory_bitflip"):
        if fault_type == "memory_bitflip":
            address = random.randint(0x20000000, 0x20010000)
            bit = random.randint(0, 31)
            self.qemu_mgr.send_debug_command(f"inject_error {address} {bit}")

    def send_debug_command(self, command):
        if self.qemu_mgr and self.qemu_mgr.process:
            self.qemu_mgr.process.stdin.write(f"{command}\n")
            self.qemu_mgr.process.stdin.flush()

    def check_recovery(self):
        log_tail = self.log_text.get("1.0", "end-1c")[-200:]
        return "ERROR_RECOVERED" in log_tail

    def update_case_result(self, name, status, time_val=None, info=None):
        self.root.after(0, lambda: self._do_update(name, status, time_val, info))
        self.total_tests += 1
        if status != "RE" or not info.startswith("故障后未恢复"):
            self.successful_tests += 1

    def calculate_survival_rate(self):
        if self.total_tests == 0:
            rate = 0.0
        else:
            rate = (self.successful_tests / self.total_tests) * 100
        self.log(f"异常注入生存率: {rate:.2f}%")

    def start_judge_thread(self):
        if self.judge_running:
            messagebox.showwarning("提示", "评测进行中...")
            return
        self.judge_running = True
        self.btn_judge.config(state="disabled")
        self.btn_stop.config(state="normal")
        threading.Thread(target=self.run_judge, daemon=True).start()

    def stop_judge(self):
        if self.executor:
            self.executor.stop()
            self.log("正在停止评测...")
        self.judge_running = False
        self.btn_judge.config(state="normal")
        self.btn_stop.config(state="disabled")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = QemuOJApp()
    app.run()