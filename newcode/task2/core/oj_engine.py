import os
import re

class OJEngine:
    @staticmethod
    def normalize_text(text):
        """标准 SPJ 处理：统一换行符，去除行末空格，去除文末空行"""
        if not text: return ""
        # 1. 统一换行
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 2. 逐行处理行末空格
        lines = [line.rstrip() for line in text.split('\n')]
        # 3. 去除文末所有空行并重新组装
        return '\n'.join(lines).strip()

    @classmethod
    def compare(cls, expected, actual):
        return cls.normalize_text(expected) == cls.normalize_text(actual)

    @staticmethod
    def get_test_cases(problem_path):
        """从 data 文件夹获取所有 .in 文件对"""
        data_dir = os.path.join(problem_path, "data")
        if not os.path.exists(data_dir): return []
        
        cases = []
        files = os.listdir(data_dir)
        in_files = sorted([f for f in files if f.endswith('.in')])
        
        for in_f in in_files:
            out_f = in_f.replace('.in', '.out')
            if out_f in files:
                cases.append({
                    "name": in_f,
                    "in_path": os.path.join(data_dir, in_f),
                    "out_path": os.path.join(data_dir, out_f)
                })
        return cases