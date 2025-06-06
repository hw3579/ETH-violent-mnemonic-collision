import random
import requests
import time
import os
import sys
import argparse
import hashlib
from mnemonic import Mnemonic
from web3 import Web3
from eth_account import Account
from colorthon import Colors
from web3.exceptions import TransactionNotFound
import concurrent.futures
import threading
import queue
import signal


# 添加命令行参数解析
parser = argparse.ArgumentParser(description='ETH Mnemonic Collision Finder - 多线程分片版本')
parser.add_argument('--shard', type=int, default=0, help='当前服务器分片ID (0-based)')
parser.add_argument('--total-shards', type=int, default=1, help='总分片数量')
parser.add_argument('--threads', type=int, default=8, help='线程数量')
parser.add_argument('--resume', action='store_true', help='是否从上次保存的进度继续')
args = parser.parse_args()

Account.enable_unaudited_hdwallet_features()

# 全局计数器，使用锁保护并发访问
counter_lock = threading.Lock()
z = 0  # 总尝试次数
ff = 0  # 找到的有余额钱包数

# 打印锁，避免输出混乱
print_lock = threading.Lock()

# 添加停止事件，用于通知线程退出
stop_event = threading.Event()

# 颜色定义
red = Colors.RED
green = Colors.GREEN
cyan = Colors.CYAN
yellow = Colors.YELLOW
reset = Colors.RESET

# 创建分片进度目录
progress_dir = "progress"
if not os.path.exists(progress_dir):
    os.makedirs(progress_dir)

# 分片相关变量
shard_id = args.shard
total_shards = args.total_shards
progress_file = f"{progress_dir}/progress_shard_{shard_id}.txt"

# 每个分片使用独立的结果文件
result_file = f"88_shard_{shard_id}.txt"

# 定义信号处理函数
def handle_interrupt(signum, frame):
    print(f"\n{yellow}接收到中断信号，正在停止所有线程...{reset}")
    stop_event.set()  # 设置停止事件通知所有线程

# 注册信号处理函数
signal.signal(signal.SIGINT, handle_interrupt)


# RPC节点管理类
class RPCNodePool:
    def __init__(self, rpc_file):
        self.nodes = []
        self.node_status = {}  # 记录节点状态和失败次数
        self.node_pools = []   # 每个线程的RPC节点池
        self.load_nodes(rpc_file)
        self.pool_lock = threading.Lock()
    
    def load_nodes(self, rpc_file):
        try:
            with open(rpc_file, "r", encoding="utf-8") as file:
                self.nodes = [url.strip() for url in file.readlines() if url.strip()]
            
            for node in self.nodes:
                self.node_status[node] = {
                    "fails": 0,
                    "last_used": 0,
                    "available": True
                }
                
            print(f"{green}已加载 {len(self.nodes)} 个RPC节点{reset}")
        except Exception as e:
            print(f"{red}无法加载RPC节点: {e}{reset}")
            sys.exit(1)
    
    def allocate_node_pools(self, num_threads):
        """为每个线程分配RPC节点池"""
        # 打乱节点顺序以随机分配
        random.shuffle(self.nodes)
        
        # 创建线程数量的节点池
        self.node_pools = [[] for _ in range(num_threads)]
        
        # 按轮询方式分配节点给各线程
        for i, node in enumerate(self.nodes):
            thread_id = i % num_threads
            self.node_pools[thread_id].append(node)
        
        # 打印分配信息
        for i, pool in enumerate(self.node_pools):
            print(f"{cyan}线程 {i+1} 分配了 {len(pool)} 个RPC节点{reset}")
    
    def get_rpc_for_thread(self, thread_id):
        """获取指定线程可用的RPC节点"""
        with self.pool_lock:
            # 获取该线程的节点池
            pool = self.node_pools[thread_id % len(self.node_pools)]
            
            # 尝试找到一个可用节点
            for node in pool:
                if self.node_status[node]["available"]:
                    # 更新使用时间
                    self.node_status[node]["last_used"] = time.time()
                    return node
            
            # 如果该线程池中没有可用节点，则重置所有节点状态并返回第一个
            for node in pool:
                self.node_status[node]["fails"] = 0
                self.node_status[node]["available"] = True
            
            return pool[0] if pool else None
    
    def mark_node_fail(self, node):
        """标记节点失败"""
        with self.pool_lock:
            if node in self.node_status:
                self.node_status[node]["fails"] += 1
                
                # 如果连续失败超过阈值，暂时标记为不可用
                if self.node_status[node]["fails"] >= 3:
                    self.node_status[node]["available"] = False
                    print(f"{yellow}RPC节点暂时不可用: {node}{reset}")
                    
                    # 60秒后自动恢复
                    threading.Timer(60, self.reset_node_status, args=[node]).start()
    
    def mark_node_success(self, node):
        """标记节点成功"""
        with self.pool_lock:
            if node in self.node_status:
                self.node_status[node]["fails"] = 0
    
    def reset_node_status(self, node):
        """重置节点状态"""
        with self.pool_lock:
            if node in self.node_status:
                self.node_status[node]["fails"] = 0
                self.node_status[node]["available"] = True
                print(f"{green}RPC节点已恢复可用: {node}{reset}")

# 初始化RPC节点池
rpc_pool = RPCNodePool("rpc.txt")
rpc_pool.allocate_node_pools(args.threads)

def CheckBalanceEthereum(address, thread_id):
    """使用线程特定的RPC节点检查余额"""
    rpc_url = rpc_pool.get_rpc_for_thread(thread_id)
    
    if not rpc_url:
        with print_lock:
            print(f"{red}线程 {thread_id} 没有可用的RPC节点{reset}")
        return None
    
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 5}))
        balance = web3.eth.get_balance(address)
        # 标记节点成功
        rpc_pool.mark_node_success(rpc_url)
        return balance
    except TransactionNotFound:
        # 标记节点成功（这是正常情况，只是地址没有交易）
        rpc_pool.mark_node_success(rpc_url)
        return 0
    except Exception as e:
        # 标记节点失败
        rpc_pool.mark_node_fail(rpc_url)
        with print_lock:
            print(f"{red}线程 {thread_id} RPC节点 {rpc_url} 错误: {e}{reset}")
        return None

def generate_eth_address_from_mnemonic(mnemonic):
    account_path = "m/44'/60'/0'/0/0"
    
    mnemo = Mnemonic("english")
    if not mnemo.check(mnemonic):
        raise ValueError(f"Invalid mnemonic: {mnemonic}")
    
    acct = Account.from_mnemonic(mnemonic, account_path=account_path)
    private_key = acct.key
    eth_address = acct.address
    return eth_address, private_key

# from fast_eth_key import generate_eth_address_from_mnemonic

# 多线程工作函数
def worker(thread_id, bip39):
    global z, ff
    
    while not stop_event.is_set():  # 检查停止事件
        # 原子操作增加计数器
        with counter_lock:
            z += 1
            current_z = z
            
            # 每100次迭代保存一次进度
            if current_z % 100 == 0:
                with open(progress_file, "w") as f:
                    f.write(f"{current_z},{ff}")
            
            # 每10000次迭代显示一次进度信息
            if current_z % 10000 == 0:
                with print_lock:
                    print(f"{yellow}进度保存: 已处理 {current_z} 个助记词, 找到 {ff} 个有效钱包{reset}")
                    
                # 定期检查停止事件
                if stop_event.is_set():
                    break
        
        rand_num = random.choice([12, 15, 18, 21, 24])
        mnemonic = " ".join(random.choice(bip39) for _ in range(rand_num))
        
        # 使用哈希函数确定这个助记词是否属于该分片处理范围
        mnemonic_hash = int(hashlib.sha256(mnemonic.encode()).hexdigest(), 16)
        if mnemonic_hash % total_shards != shard_id:
            continue  # 跳过不属于该分片的助记词
        
        try:
            eth_addr, private_key = generate_eth_address_from_mnemonic(mnemonic)
            eth_bal = CheckBalanceEthereum(eth_addr, thread_id)
            
            if eth_bal is None:
                continue
                
            eth_balance = eth_bal / 10**18
            
            with print_lock:
                addr_space = " " * (44 - len(eth_addr))
                print(f"线程[{thread_id}] ({current_z}) 分片[{shard_id+1}/{total_shards}] ETH: {cyan}{eth_addr}{reset}{addr_space}[Balance: {cyan}{eth_balance}{reset}]")
                print(f"Mnemonic: {yellow}{mnemonic}{reset}")
                print(f"Private Key: {private_key.hex()}")
                print(f"{'-' * 66}")
            
            if eth_balance > 0:
                with counter_lock:
                    ff += 1
                
                with open(result_file, "a", encoding="utf-8") as dr:
                    dr.write(f"ETH: {eth_addr} | Balance: {eth_balance}\n"
                             f"Mnemonic: {mnemonic}\n"
                             f"Private Key: {private_key.hex()}\n\n")
                
                # 同时保存到汇总文件
                with open("88.txt", "a", encoding="utf-8") as dr:
                    dr.write(f"ETH: {eth_addr} | Balance: {eth_balance}\n"
                             f"Mnemonic: {mnemonic}\n"
                             f"Private Key: {private_key.hex()}\n"
                             f"Found by Shard: {shard_id}, Thread: {thread_id}\n\n")
                
                with print_lock:
                    print(f"\n{green}🚨 找到有余额的钱包! 详细信息已保存到{result_file}和88.txt文件 🚨{reset}\n")
                
                # 立即保存进度
                with open(progress_file, "w") as f:
                    f.write(f"{current_z},{ff}")
        
        except ValueError:
            continue
        except Exception as e:
            with print_lock:
                print(f"{red}线程 {thread_id} 发生未知错误: {e}{reset}")
            continue
    
    with print_lock:
        print(f"{yellow}线程 {thread_id} 已停止{reset}")

# 主程序
def main():
    global z, ff
    
    print(f"{cyan}运行于分片 {shard_id+1}/{total_shards}{reset}")
    print(f"{cyan}按 Ctrl+C 可以随时停止程序并保存进度{reset}")
    
    # 检查必要文件
    if not os.path.exists("bip39.txt"):
        print(f"{red}未找到bip39.txt文件{reset}")
        sys.exit(1)
    
    if not os.path.exists(result_file):
        try:
            with open(result_file, "w", encoding="utf-8") as dr:
                dr.write("")
            print(f"{green}成功创建 {result_file} 文件{reset}")
        except Exception as e:
            print(f"{red}无法创建 {result_file} 文件: {e}{reset}")
    
    # 从进度文件恢复或初始化计数
    if args.resume and os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as f:
                saved_data = f.read().strip().split(",")
                z = int(saved_data[0])
                if len(saved_data) > 1:
                    ff = int(saved_data[1])
            print(f"{green}从计数 {z} 恢复搜索进度，已找到 {ff} 个有效钱包{reset}")
        except Exception as e:
            print(f"{red}无法恢复进度: {e}，将从头开始{reset}")
            z = 0
            ff = 0

    # 读取BIP39词典
    try:
        with open("bip39.txt", "r", encoding="utf-8") as b_read:
            bip39 = [line.strip() for line in b_read.readlines() if line.strip()]
        print(f"{green}已加载 {len(bip39)} 个BIP39单词{reset}")
    except Exception as e:
        print(f"{red}Failed to read bip39.txt file: {e}{reset}")
        sys.exit(1)

    print(f"{yellow}分片信息: 当前分片 {shard_id+1}/{total_shards}{reset}")
    print(f"{yellow}进度文件: {progress_file}{reset}")
    print(f"{yellow}结果文件: {result_file}{reset}")
    print(f"{yellow}线程数量: {args.threads}{reset}")
    print(f"{'-' * 66}")

    # 使用线程池执行多线程搜索
    try:
        threads = []
        for i in range(args.threads):
            thread = threading.Thread(
                target=worker, 
                args=(i, bip39),
                daemon=True
            )
            threads.append(thread)
            thread.start()
            print(f"{green}启动线程 {i+1}{reset}")
        
        # 等待所有线程完成或收到停止信号
        while any(t.is_alive() for t in threads):
            if stop_event.is_set():
                break
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        # 设置停止事件
        stop_event.set()
        print(f"\n{yellow}收到键盘中断，正在停止线程...{reset}")
    finally:
        # 确保停止事件被设置
        stop_event.set()
        
        # 等待所有线程结束（最多等待5秒）
        for thread in threads:
            thread.join(timeout=5)
        
        # 保存最终进度
        with open(progress_file, "w") as f:
            f.write(f"{z},{ff}")
        
        print(f"\n{green}进度已保存: 已处理 {z} 个助记词, 找到 {ff} 个有效钱包{reset}")
        print(f"{green}可使用 --resume 参数从当前进度继续{reset}")

if __name__ == "__main__":
    main()