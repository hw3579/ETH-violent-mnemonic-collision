import os
import sys
import hashlib
import argparse
import random
import math
from colorthon import Colors

# 颜色定义
red = Colors.RED
green = Colors.GREEN
cyan = Colors.CYAN
yellow = Colors.YELLOW
reset = Colors.RESET

# 命令行参数
parser = argparse.ArgumentParser(description='ETH Mnemonic Collision Finder - 分片迁移工具')
parser.add_argument('--old-shard', type=int, required=True, help='原始分片ID')
parser.add_argument('--old-total', type=int, required=True, help='原始总分片数')
parser.add_argument('--new-shard-exp', type=int, required=True, help='新的分片数量指数 (10^x)')
parser.add_argument('--new-shard-start', type=int, required=True, help='新分片起始ID')
parser.add_argument('--new-shard-count', type=int, required=True, help='要创建的新分片数量')
parser.add_argument('--seed', type=int, help='随机种子(可选)')
args = parser.parse_args()

# 验证参数
if args.new_shard_exp <= 0 or args.new_shard_exp > 30:
    print(f"{red}错误: 新分片数量指数必须在1到30之间{reset}")
    sys.exit(1)

new_total_shards = 10 ** args.new_shard_exp
if args.new_shard_start < 0 or args.new_shard_start >= new_total_shards:
    print(f"{red}错误: 新分片起始ID必须在0到{new_total_shards-1}之间{reset}")
    sys.exit(1)

if args.new_shard_count <= 0:
    print(f"{red}错误: 新分片数量必须大于0{reset}")
    sys.exit(1)

if args.new_shard_start + args.new_shard_count > new_total_shards:
    print(f"{red}错误: 新分片范围超出总分片数{reset}")
    sys.exit(1)

# 创建目录
progress_dir = "progress"
if not os.path.exists(progress_dir):
    os.makedirs(progress_dir)

# 读取原始进度
old_progress_file = f"{progress_dir}/progress_shard_{args.old_shard}.txt"
if not os.path.exists(old_progress_file):
    print(f"{red}错误: 原始进度文件不存在: {old_progress_file}{reset}")
    sys.exit(1)

with open(old_progress_file, "r") as f:
    progress_data = f.read().strip().split(",")
    processed_count = int(progress_data[0])
    found_count = int(progress_data[1]) if len(progress_data) > 1 else 0

print(f"{green}原始进度: 已处理 {processed_count} 个助记词, 找到 {found_count} 个有效钱包{reset}")

# 设置随机种子，确保重现性
if args.seed:
    random.seed(args.seed)
else:
    # 使用原始分片和处理计数生成种子
    seed = args.old_shard * 10000000 + processed_count
    random.seed(seed)
    print(f"{yellow}使用自动生成的种子: {seed}{reset}")

# 计算新分片方案下每个分片的映射比例
old_shard_ratio = 1.0 / args.old_total
new_shard_ratio = 1.0 / new_total_shards

# 计算转移因子 - 每个旧分片对应多少个新分片
transfer_factor = args.old_total / new_total_shards

print(f"{cyan}分片迁移信息:{reset}")
print(f"  原始分片: {args.old_shard}/{args.old_total}")
print(f"  新分片系统: 总共 10^{args.new_shard_exp} = {new_total_shards} 个分片")
print(f"  迁移范围: 从分片 {args.new_shard_start} 开始，共 {args.new_shard_count} 个分片")
print(f"  转移因子: {transfer_factor}")

# 估算每个新分片应分配的进度
estimated_progress = int(processed_count * transfer_factor)
print(f"{yellow}每个新分片的估计进度: 约 {estimated_progress} 个助记词{reset}")

# 创建新分片进度文件
created_files = []
for i in range(args.new_shard_count):
    new_shard_id = args.new_shard_start + i
    new_progress_file = f"{progress_dir}/progress_shard_{new_shard_id}.txt"
    
    # 对每个分片，随机化进度值，避免所有分片从完全相同的点开始
    # 在估算值的 95%-105% 范围内随机化
    randomized_progress = int(estimated_progress * random.uniform(0.95, 1.05))
    
    # 确保进度不超过原始进度
    randomized_progress = min(randomized_progress, processed_count)
    
    with open(new_progress_file, "w") as f:
        f.write(f"{randomized_progress},{found_count}")
    
    created_files.append((new_shard_id, randomized_progress))

# 创建分片映射配置文件，用于记录迁移信息
mapping_file = f"{progress_dir}/shard_migration_map.txt"
with open(mapping_file, "a") as f:
    f.write(f"原始分片 {args.old_shard}/{args.old_total} -> 新分片范围 {args.new_shard_start}-{args.new_shard_start+args.new_shard_count-1}/{new_total_shards}\n")
    f.write(f"进度: {processed_count} -> 估计每分片: {estimated_progress}\n")
    f.write(f"种子: {args.seed if args.seed else seed}\n")
    f.write("---\n")

# 生成启动命令示例
print(f"\n{green}已成功创建 {len(created_files)} 个新分片进度文件:{reset}")
for shard_id, progress in created_files:
    print(f"  分片 {shard_id}: {progress} 个助记词")

print(f"\n{yellow}使用以下命令启动新分片:{reset}")
for shard_id, _ in created_files:
    print(f"python eth.py --shard {shard_id} --total-shards {new_total_shards} --threads 15 --resume")

print(f"\n{green}迁移信息已保存到: {mapping_file}{reset}")
print(f"{yellow}注意: 请使用 --seed {args.seed if args.seed else seed} 参数确保随机数生成一致性{reset}")