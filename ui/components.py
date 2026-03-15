import tkinter as tk
from tkinter import ttk, font

class OJComponents:
    @staticmethod
    def create_editor(parent):
        custom_font = font.Font(family="Consolas", size=12)
        editor = tk.Text(parent, font=custom_font, undo=True, bg="#ffffff")
        tab_size = custom_font.measure(' ' * 4)
        editor.config(tabs=tab_size)
        return editor

    @staticmethod
    def create_result_table(parent):
        columns = ("id", "status", "time", "info")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=12)
        tree.heading("id", text="测试点")
        tree.heading("status", text="结果")
        tree.heading("time", text="耗时(ms)")
        tree.heading("info", text="备注")
        tree.column("id", width=100, anchor="center")
        tree.column("status", width=80, anchor="center")
        tree.column("time", width=80, anchor="center")
        tree.column("info", width=150, anchor="w")
        return tree

    @staticmethod
    def create_log_viewer(parent):
        log_frame = ttk.LabelFrame(parent, text="运行日志")
        log_text = tk.Text(log_frame, height=6, state="disabled", bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
        log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        log_text.pack(side="left", fill="both", expand=True)
        return log_frame, log_text
