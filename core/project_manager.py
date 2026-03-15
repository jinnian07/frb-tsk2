# core/project_manager.py
import os
import shutil

def create_user_project(problem_id, user_id="default"):
    """
    复制题目模板到用户专属工程目录
    :param problem_id: 题目ID（如 "P0001"）
    :param user_id: 用户ID（默认为 "default"）
    :return: 用户代码文件路径（如 user_projects/default/P0001/main.c）
    """
    # 1. 源模板路径（题目中的 std.c）
    src_template = os.path.join(problem_id, "std.c")  # 假设模板文件名为 std.c
    
    # 2. 用户工程目标路径
    dest_dir = os.path.join("user_projects", user_id, problem_id)
    os.makedirs(dest_dir, exist_ok=True)  # 自动创建多级目录（如 user_projects/default/P0001/）
    
    # 3. 复制文件（将 std.c 复制为 main.c）
    dest_file = os.path.join(dest_dir, "main.c")
    shutil.copy(src_template, dest_file)
    
    return dest_file  # 返回用户代码文件路径