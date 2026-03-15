import os
import json
import re

def load_config(config_path="config.json"):
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def scan_problems():
    """扫描所有题目文件夹（如 P0001、P0002）"""
    problems_dir = "."  # 当前目录（即 task2/ 根目录）
    problem_list = []
    
    # 遍历根目录下的所有文件/文件夹
    for entry in os.listdir(problems_dir):
        full_path = os.path.join(problems_dir, entry)
        
        # 如果是目录，且以 "P" 开头，长度为5（如 P0001）
        if os.path.isdir(full_path) and entry.startswith("P") and len(entry) == 5:
            problem_list.append(entry)
    
    return sorted(problem_list)

def get_problem_info(problem_id):
    problem_dir = os.path.join(".", problem_id)
    info = {
        "id": problem_id,
        "md_path": os.path.join(problem_dir, "题面.md"),
        "data_dir": os.path.join(problem_dir, "data"),
        "exists": os.path.isdir(problem_dir)
    }
    return info
