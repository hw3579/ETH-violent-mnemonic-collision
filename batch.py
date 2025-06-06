import random
import requests
import time
import os
import sys
import argparse
import hashlib
import json
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
parser = argparse.ArgumentParser(description='ETH Mnemonic Collision Finder - 批量查询优化版')
parser.add_argument('--shard', type=int, default=0, help='当前服务器分片ID (0-based)')
parser.add_argument('--total-shards', type=int, default=1, help='总分片数量')
parser.add_argument('--gen-threads', type=int, default=4, help='生成助记词的线程数量')
parser.add_argument('--query-threads', type=int, default=4, help='查询线程数量')
parser.add_argument('--resume', action='store_true', help='是否从上次保存的进度继续')
parser.add_argument('--queue-size', type=int, default=10000, help='待查询队列大小')
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

# 地址生成和查询队列
address_queue = queue.Queue(maxsize=args.queue_size)

# RPC节点管理类 (增强版支持批量查询)
class RPCNodePool:
    def __init__(self, rpc_file, batch_info_file=None):
        self.nodes = []
        self.node_status = {}  # 记录节点状态和失败次数
        self.batch_info = {}   # 记录节点批量查询能力
        self.load_nodes(rpc_file)
        self.load_batch_info(batch_info_file)
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
    
    def load_batch_info(self, batch_info_file):
        # 默认值 - 所有节点默认支持单次查询
        for node in self.nodes:
            self.batch_info[node] = {
                "batch_support": False,
                "max_batch_size": 1,
                "speedup": 1.0
            }
        
        # 如果提供了批量信息文件，从中加载
        if batch_info_file and os.path.exists(batch_info_file):
            try:
                with open(batch_info_file, "r") as f:
                    data = json.load(f)
                
                for item in data:
                    url = item.get("url")
                    if url in self.nodes:
                        self.batch_info[url] = {
                            "batch_support": item.get("batch_request", False),
                            "max_batch_size": item.get("max_batch_size", 1) if item.get("batch_request", False) else 1,
                            "speedup": item.get("speedup", 1.0) if item.get("batch_request", False) else 1.0
                        }
                
                print(f"{green}已加载 {len(data)} 个节点的批量查询信息{reset}")
                
                # 打印支持批量查询的节点信息
                batch_nodes = [node for node in self.nodes if self.batch_info[node]["batch_support"]]
                print(f"{green}支持批量查询的节点: {len(batch_nodes)}/{len(self.nodes)}{reset}")
                for node in batch_nodes:
                    max_size = self.batch_info[node]["max_batch_size"]
                    speedup = self.batch_info[node]["speedup"]
                    print(f"{cyan}节点 {node} 支持最大批量 {max_size}, 加速比 {speedup:.2f}x{reset}")
                    
            except Exception as e:
                print(f"{yellow}加载批量查询信息失败: {e}, 使用默认设置{reset}")
    
    def get_best_batch_node(self):
        """获取当前最佳可用的批量查询节点"""
        with self.pool_lock:
            # 首先尝试找到支持批量查询的可用节点
            batch_nodes = [(node, info) for node, info in self.batch_info.items() 
                           if info["batch_support"] and self.node_status[node]["available"]]
            
            # 按批量大小和加速比排序
            batch_nodes.sort(key=lambda x: (x[1]["max_batch_size"], x[1]["speedup"]), reverse=True)
            
            # 如果有支持批量的节点，返回最佳的
            if batch_nodes:
                node, info = batch_nodes[0]
                self.node_status[node]["last_used"] = time.time()
                return node, info["max_batch_size"]
            
            # 如果没有批量节点可用，返回任意可用节点
            for node in self.nodes:
                if self.node_status[node]["available"]:
                    self.node_status[node]["last_used"] = time.time()
                    return node, 1
            
            # 如果没有可用节点，重置所有节点并返回第一个
            for node in self.nodes:
                self.node_status[node]["fails"] = 0
                self.node_status[node]["available"] = True
            
            return self.nodes[0] if self.nodes else (None, 0), 1
    
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

# 初始化RPC节点池，传入批量查询信息
rpc_pool = RPCNodePool("rpc.txt", "rpc_test_results/all_rpc_results.json")

def batch_check_balances(addresses, thread_id):
    """批量检查多个地址的余额"""
    rpc_url, batch_size = rpc_pool.get_best_batch_node()
    
    if not rpc_url:
        with print_lock:
            print(f"{red}线程 {thread_id} 没有可用的RPC节点{reset}")
        return None
    
    # 限制批量大小不超过节点支持的最大值
    batch_size = min(batch_size, len(addresses))
    
    # 如果只有1个地址或节点不支持批量，使用单个查询
    if batch_size == 1:
        try:
            web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
            results = {}
            
            for addr in addresses:
                try:
                    balance = web3.eth.get_balance(addr)
                    results[addr] = balance
                except Exception as e:
                    results[addr] = 0
            
            # 标记节点成功
            rpc_pool.mark_node_success(rpc_url)
            return results
        except Exception as e:
            # 标记节点失败
            rpc_pool.mark_node_fail(rpc_url)
            with print_lock:
                print(f"{red}线程 {thread_id} RPC节点 {rpc_url} 错误: {e}{reset}")
            return None
    
    # 批量查询
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 20}))
        
        # 准备批量请求
        batch_request = []
        for i, addr in enumerate(addresses):
            batch_request.append({
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [Web3.to_checksum_address(addr), "latest"],
                "id": i + 1
            })
        
        # 发送批量请求
        response = requests.post(
            rpc_url,
            json=batch_request,
            headers={"Content-Type": "application/json"},
            timeout=20
        )
        
        # 解析响应
        if response.status_code == 200:
            results = {}
            response_data = response.json()
            
            # 如果响应不是列表，可能是节点不支持批量但返回了单个结果
            if not isinstance(response_data, list):
                response_data = [response_data]
            
            for item in response_data:
                if "result" in item and "id" in item:
                    addr_index = item["id"] - 1
                    if addr_index < len(addresses):
                        addr = addresses[addr_index]
                        balance_hex = item["result"]
                        try:
                            balance = int(balance_hex, 16)
                            results[addr] = balance
                        except:
                            results[addr] = 0
            
            # 标记节点成功
            rpc_pool.mark_node_success(rpc_url)
            
            # 对于未在结果中的地址，设置余额为0
            for addr in addresses:
                if addr not in results:
                    results[addr] = 0
                    
            return results
        else:
            # 标记节点失败
            rpc_pool.mark_node_fail(rpc_url)
            with print_lock:
                print(f"{red}线程 {thread_id} RPC节点 {rpc_url} 返回状态码: {response.status_code}{reset}")
            return None
    except Exception as e:
        # 标记节点失败
        rpc_pool.mark_node_fail(rpc_url)
        with print_lock:
            print(f"{red}线程 {thread_id} RPC节点 {rpc_url} 批量查询错误: {e}{reset}")
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

# 地址生成工作线程
def generator_worker(thread_id, bip39):
    global z
    
    while not stop_event.is_set():
        # 检查队列是否已满
        if address_queue.full():
            time.sleep(0.1)  # 队列满了，等待查询线程消费
            continue
            
        # 原子操作增加计数器
        with counter_lock:
            z += 1
            current_z = z
            
            # 每1000次迭代保存一次进度
            if current_z % 1000 == 0:
                with open(progress_file, "w") as f:
                    f.write(f"{current_z},{ff}")
            
            # 每10000次迭代显示一次进度信息
            if current_z % 10000 == 0:
                with print_lock:
                    print(f"{yellow}进度保存: 已处理 {current_z} 个助记词, 找到 {ff} 个有效钱包{reset}")
        
        rand_num = random.choice([12, 15, 18, 21, 24])
        mnemonic = " ".join(random.choice(bip39) for _ in range(rand_num))
        
        # 使用哈希函数确定这个助记词是否属于该分片处理范围
        mnemonic_hash = int(hashlib.sha256(mnemonic.encode()).hexdigest(), 16)
        if mnemonic_hash % total_shards != shard_id:
            continue  # 跳过不属于该分片的助记词
        
        try:
            eth_addr, private_key = generate_eth_address_from_mnemonic(mnemonic)
            
            # 将结果放入队列
            address_info = {
                "address": eth_addr,
                "private_key": private_key,
                "mnemonic": mnemonic,
                "count": current_z
            }
            
            # 尝试放入队列，超时后继续生成新地址
            try:
                address_queue.put(address_info, timeout=1)
            except queue.Full:
                continue
                
        except ValueError:
            continue
        except Exception as e:
            with print_lock:
                print(f"{red}生成线程 {thread_id} 发生未知错误: {e}{reset}")
            continue
    
    with print_lock:
        print(f"{yellow}生成线程 {thread_id} 已停止{reset}")

# 查询工作线程
def query_worker(thread_id):
    global ff
    batch_size = 10  # 默认批量大小
    
    while not stop_event.is_set():
        # 从队列获取一批地址
        addresses = []
        address_infos = {}
        
        # 尝试获取批量地址，但不阻塞太久
        try:
            # 先获取一个
            address_info = address_queue.get(timeout=1)
            addresses.append(address_info["address"])
            address_infos[address_info["address"]] = address_info
            address_queue.task_done()
            
            # 然后尝试获取更多，直到达到批量大小或队列为空
            for _ in range(batch_size - 1):
                try:
                    address_info = address_queue.get(block=False)
                    addresses.append(address_info["address"])
                    address_infos[address_info["address"]] = address_info
                    address_queue.task_done()
                except queue.Empty:
                    break
        except queue.Empty:
            # 队列为空，等待片刻
            time.sleep(0.1)
            continue
        
        # 如果成功获取了地址，进行批量查询
        if addresses:
            balances = batch_check_balances(addresses, thread_id)
            
            if balances is None:
                # 查询失败，将地址放回队列重试
                for addr in addresses:
                    try:
                        address_queue.put(address_infos[addr], timeout=1)
                    except queue.Full:
                        pass  # 队列满了，丢弃
                continue
            
            # 处理查询结果
            for addr, balance in balances.items():
                if addr in address_infos:
                    address_info = address_infos[addr]
                    eth_balance = balance / 10**18
                    
                    with print_lock:
                        addr_space = " " * (44 - len(addr))
                        print(f"查询线程[{thread_id}] ({address_info['count']}) 分片[{shard_id+1}/{total_shards}] ETH: {cyan}{addr}{reset}{addr_space}[Balance: {cyan}{eth_balance}{reset}]")
                        print(f"Mnemonic: {yellow}{address_info['mnemonic']}{reset}")
                        print(f"Private Key: {address_info['private_key'].hex()}")
                        print(f"{'-' * 66}")
                    
                    # 如果发现有余额，保存结果
                    if eth_balance > 0:
                        with counter_lock:
                            ff += 1
                        
                        with open(result_file, "a", encoding="utf-8") as dr:
                            dr.write(f"ETH: {addr} | Balance: {eth_balance}\n"
                                    f"Mnemonic: {address_info['mnemonic']}\n"
                                    f"Private Key: {address_info['private_key'].hex()}\n\n")
                        
                        # 同时保存到汇总文件
                        with open("88.txt", "a", encoding="utf-8") as dr:
                            dr.write(f"ETH: {addr} | Balance: {eth_balance}\n"
                                    f"Mnemonic: {address_info['mnemonic']}\n"
                                    f"Private Key: {address_info['private_key'].hex()}\n"
                                    f"Found by Shard: {shard_id}, Thread: {thread_id}\n\n")
                        
                        with print_lock:
                            print(f"\n{green}🚨 找到有余额的钱包! 详细信息已保存到{result_file}和88.txt文件 🚨{reset}\n")
                        
                        # 立即保存进度
                        with open(progress_file, "w") as f:
                            f.write(f"{z},{ff}")
    
    with print_lock:
        print(f"{yellow}查询线程 {thread_id} 已停止{reset}")

# 主程序
def main():
    global z, ff
    
    print(f"{cyan}批量查询优化版 - 运行于分片 {shard_id+1}/{total_shards}{reset}")
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
    print(f"{yellow}生成线程: {args.gen_threads}, 查询线程: {args.query_threads}{reset}")
    print(f"{yellow}队列大小: {args.queue_size}{reset}")
    print(f"{'-' * 66}")

    # 启动生成和查询线程
    try:
        # 启动生成线程
        gen_threads = []
        for i in range(args.gen_threads):
            thread = threading.Thread(
                target=generator_worker, 
                args=(i, bip39),
                daemon=True
            )
            gen_threads.append(thread)
            thread.start()
            print(f"{green}启动生成线程 {i+1}{reset}")
        
        # 启动查询线程
        query_threads = []
        for i in range(args.query_threads):
            thread = threading.Thread(
                target=query_worker, 
                args=(i,),
                daemon=True
            )
            query_threads.append(thread)
            thread.start()
            print(f"{green}启动查询线程 {i+1}{reset}")
        
        # 等待所有线程完成或收到停止信号
        all_threads = gen_threads + query_threads
        while any(t.is_alive() for t in all_threads):
            if stop_event.is_set():
                break
            
            # 打印队列状态
            if not stop_event.is_set() and time.time() % 60 < 1:  # 大约每分钟
                with print_lock:
                    print(f"{cyan}队列状态: {address_queue.qsize()}/{args.queue_size} ({address_queue.qsize()/args.queue_size*100:.1f}%){reset}")
                time.sleep(1)  # 避免打印多次
            
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        # 设置停止事件
        stop_event.set()
        print(f"\n{yellow}收到键盘中断，正在停止线程...{reset}")
    finally:
        # 确保停止事件被设置
        stop_event.set()
        
        # 等待所有线程结束（最多等待5秒）
        for thread in all_threads:
            thread.join(timeout=5)
        
        # 保存最终进度
        with open(progress_file, "w") as f:
            f.write(f"{z},{ff}")
        
        print(f"\n{green}进度已保存: 已处理 {z} 个助记词, 找到 {ff} 个有效钱包{reset}")
        print(f"{green}可使用 --resume 参数从当前进度继续{reset}")

if __name__ == "__main__":
    main()