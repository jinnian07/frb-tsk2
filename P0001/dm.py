import random
import os
import subprocess

# 创建测试数据目录
os.makedirs("data", exist_ok=True)

# 生成10个测试点
for i in range(1, 11):
    with open(f"data/train{i:02d}.in", "w") as f:
        if i == 10:
            # 第10组：极端情况
            # k = 0, 1, 2^n-1, 2^n-2，对32和64分别做，共8组
            test_cases = [
                (32, 0),
                (32, 1),
                (32, (1 << 32) - 1),
                (32, (1 << 32) - 2),
                (64, 0),
                (64, 1),
                (64, (1 << 64) - 1),
                (64, (1 << 64) - 2),
            ]
            for n, k in test_cases:
                f.write(f"{n} {k}\n")
        else:
            # 生成10组数据
            for j in range(10):
                # 前50%的测试点(1-5)只有n=32
                if i <= 5:
                    n = 32
                    # k的范围: 0 到 2^32-1
                    k = random.randint(0, (1 << 32) - 1)
                else:
                    # 后50%的测试点(6-10)混合n=32和n=64
                    if random.random() < 0.5:
                        n = 32
                        k = random.randint(0, (1 << 32) - 1)
                    else:
                        n = 64
                        # k的范围: 0 到 2^64-1
                        k = random.randint(0, (1 << 64) - 1)
                
                f.write(f"{n} {k}\n")
    
    print(f"已生成 data/train{i:02d}.in")

print("\n开始生成输出文件...")

# 使用std.exe生成输出文件
for i in range(1, 11):
    input_file = f"data/train{i:02d}.in"
    output_file = f"data/train{i:02d}.out"
    
    with open(input_file, "r") as fin, open(output_file, "w") as fout:
        result = subprocess.run(
            ["std.exe"],
            stdin=fin,
            stdout=fout,
            text=True
        )
    
    print(f"已生成 data/train{i:02d}.out")

print("\n所有数据生成完成！")
