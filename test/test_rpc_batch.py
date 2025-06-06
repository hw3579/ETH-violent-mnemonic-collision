import requests
import json
import time
import random
from concurrent.futures import ThreadPoolExecutor
from colorthon import Colors

# 颜色定义
red = Colors.RED
green = Colors.GREEN
cyan = Colors.CYAN
yellow = Colors.YELLOW
reset = Colors.RESET

# 测试地址列表 (一些随机的以太坊地址)
TEST_ADDRESSES = [
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",  # vitalik.eth
    "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B", 
    "0xB8c77482e45F1F44dE1745F52C74426C631bDD52",
    "0x7Be8076f4EA4A4AD08075C2508e481d6C946D12b",
    "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
]

def load_rpc_nodes(file_path):
    """加载RPC节点列表"""
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return [url.strip() for url in file.readlines() if url.strip() and not url.strip().startswith("//")]
    except Exception as e:
        print(f"{red}无法加载RPC节点: {e}{reset}")
        return []

def test_single_request(rpc_url, address):
    """测试单个请求"""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [address, "latest"],
        "id": 1
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        start_time = time.time()
        response = requests.post(rpc_url, json=payload, headers=headers, timeout=5)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            if "result" in result:
                return True, elapsed
        
        return False, elapsed
    except Exception as e:
        return False, 0

def test_batch_request(rpc_url, addresses, batch_size=5):
    """测试批量请求"""
    batch_payload = []
    
    # 构建批量请求
    for i, address in enumerate(addresses[:batch_size]):
        batch_payload.append({
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [address, "latest"],
            "id": i + 1
        })
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        start_time = time.time()
        response = requests.post(rpc_url, json=batch_payload, headers=headers, timeout=10)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) == len(batch_payload):
                return True, elapsed, len(result)
        
        return False, elapsed, 0
    except Exception as e:
        return False, 0, 0

def test_rpc_node(rpc_url):
    """测试RPC节点单请求和批量请求支持"""
    print(f"\n{cyan}测试节点: {rpc_url}{reset}")
    
    # 测试单个请求
    single_success, single_time = test_single_request(rpc_url, random.choice(TEST_ADDRESSES))
    
    if not single_success:
        print(f"{red}节点无法处理单个请求，跳过批量测试{reset}")
        return {
            "url": rpc_url,
            "single_request": False,
            "batch_request": False,
            "single_time": 0,
            "batch_time": 0,
            "max_batch_size": 0
        }
    
    print(f"{green}单个请求成功 - 耗时: {single_time:.4f}秒{reset}")
    
    # 测试不同大小的批量请求
    batch_sizes = [2, 5, 10, 20]
    max_batch = 0
    batch_success = False
    batch_time = 0
    
    for size in batch_sizes:
        print(f"测试批量大小: {size}...", end="", flush=True)
        success, elapsed, result_size = test_batch_request(rpc_url, TEST_ADDRESSES, size)
        
        if success:
            batch_success = True
            batch_time = elapsed
            max_batch = size
            print(f"{green}成功 - 耗时: {elapsed:.4f}秒{reset}")
        else:
            print(f"{red}失败{reset}")
            break
    
    if batch_success:
        print(f"{green}节点支持批量请求! 最大测试批量: {max_batch}{reset}")
        print(f"{yellow}性能比较: 单个请求: {single_time:.4f}秒, 批量请求({max_batch}个): {batch_time:.4f}秒{reset}")
        print(f"{yellow}平均每个请求时间: 单个: {single_time:.4f}秒, 批量: {batch_time/max_batch:.4f}秒{reset}")
        print(f"{yellow}性能提升: {(single_time*max_batch/batch_time):.2f}倍{reset}")
    else:
        print(f"{red}节点不支持批量请求{reset}")
    
    return {
        "url": rpc_url,
        "single_request": single_success,
        "batch_request": batch_success,
        "single_time": single_time,
        "batch_time": batch_time,
        "max_batch_size": max_batch
    }

def main():
    rpc_nodes = load_rpc_nodes("rpc.txt")
    
    if not rpc_nodes:
        print(f"{red}没有找到RPC节点，请检查rpc.txt文件{reset}")
        return
    
    print(f"{green}加载了 {len(rpc_nodes)} 个RPC节点{reset}")
    print(f"{yellow}开始测试RPC节点批量请求支持...{reset}")
    
    results = []
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        # 用5个线程并行测试所有节点
        futures = {executor.submit(test_rpc_node, node): node for node in rpc_nodes}
        for future in futures:
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"{red}测试节点时出错: {e}{reset}")
    
    # 结果汇总
    batch_supported = [r for r in results if r["batch_request"]]
    
    print(f"\n{'-'*80}")
    print(f"{cyan}测试完成! 结果汇总:{reset}")
    print(f"{green}测试节点总数: {len(results)}{reset}")
    print(f"{green}支持单个请求的节点数: {len([r for r in results if r['single_request']])}{reset}")
    print(f"{green}支持批量请求的节点数: {len(batch_supported)}{reset}")
    
    if batch_supported:
        print(f"\n{yellow}支持批量请求的节点:{reset}")
        for node in batch_supported:
            print(f"{green}- {node['url']} (最大批量: {node['max_batch_size']}, 性能提升: {(node['single_time']*node['max_batch_size']/node['batch_time']):.2f}倍){reset}")
        
        # 输出到文件
        with open("batch_rpc_nodes.txt", "w", encoding="utf-8") as f:
            for node in batch_supported:
                f.write(f"{node['url']}\n")
        
        print(f"\n{green}已将支持批量请求的节点保存到 batch_rpc_nodes.txt{reset}")

if __name__ == "__main__":
    main()