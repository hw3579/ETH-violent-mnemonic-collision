import requests
import json
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor
from colorthon import Colors

# 颜色定义
red = Colors.RED
green = Colors.GREEN
cyan = Colors.CYAN
yellow = Colors.YELLOW
reset = Colors.RESET

# 创建一个专门用于保存测试结果的目录
results_dir = "rpc_test_results"
if not os.path.exists(results_dir):
    os.makedirs(results_dir)

# 初始的测试地址列表
TEST_ADDRESSES = [
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",  # vitalik.eth
    "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B", 
    "0xB8c77482e45F1F44dE1745F52C74426C631bDD52",
    "0x7Be8076f4EA4A4AD08075C2508e481d6C946D12b",
    "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
]

print(f"{yellow}开始生成随机测试地址...{reset}")

# 扩展地址列表到10000个以支持超大批量测试
while len(TEST_ADDRESSES) < 10000:
    # 生成随机以太坊地址
    addr = "0x" + ''.join(random.choice("0123456789abcdef") for _ in range(40))
    if addr not in TEST_ADDRESSES:
        TEST_ADDRESSES.append(addr)
    
    # 每生成1000个显示一次进度
    if len(TEST_ADDRESSES) % 1000 == 0:
        print(f"{green}已生成 {len(TEST_ADDRESSES)} 个测试地址{reset}")

print(f"{green}已生成完毕，共 {len(TEST_ADDRESSES)} 个测试地址{reset}")

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
        response = requests.post(rpc_url, json=payload, headers=headers, timeout=10)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            if "result" in result:
                return True, elapsed
        
        return False, elapsed
    except Exception as e:
        print(f"{red}单请求错误 ({rpc_url}): {e}{reset}")
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
        # 对于大批量请求增加超时时间
        timeout = min(30, 5 + batch_size / 500)  # 动态超时：基础5秒+每500个请求增加1秒，最大30秒
        
        response = requests.post(rpc_url, json=batch_payload, headers=headers, timeout=timeout)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) == len(batch_payload):
                return True, elapsed, len(result)
        
        return False, elapsed, 0
    except Exception as e:
        print(f"{red}批量请求错误 ({rpc_url}, 批量: {batch_size}): {e}{reset}")
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
            "batch_10000": False,
            "single_time": 0,
            "batch_time": 0,
            "max_batch_size": 0
        }
    
    print(f"{green}单个请求成功 - 耗时: {single_time:.4f}秒{reset}")
    
    # 测试逐步增大的批量请求
    batch_sizes = [10, 100, 500, 1000, 2000, 5000, 10000]
    max_batch = 0
    batch_success = False
    batch_time = 0
    batch_10000_success = False
    
    # 测试每个批量大小
    for size in batch_sizes:
        print(f"测试批量大小: {size}...", end="", flush=True)
        success, elapsed, result_size = test_batch_request(rpc_url, TEST_ADDRESSES, size)
        
        if success:
            batch_success = True
            batch_time = elapsed
            max_batch = size
            
            if size == 10000:
                batch_10000_success = True
                
            print(f"{green}成功 - 耗时: {elapsed:.4f}秒{reset}")
            
            # 为了避免过度使用节点，每次成功测试后暂停几秒
            if size >= 1000:
                pause_time = min(5, size / 2000)  # 根据批量大小决定暂停时间
                print(f"{yellow}暂停 {pause_time:.1f} 秒以避免节点限流...{reset}")
                time.sleep(pause_time)
        else:
            print(f"{red}失败{reset}")
            break
    
    if batch_success:
        print(f"{green}节点支持批量请求! 最大测试批量: {max_batch}{reset}")
        print(f"{yellow}性能比较: 单个请求: {single_time:.4f}秒, 批量请求({max_batch}个): {batch_time:.4f}秒{reset}")
        print(f"{yellow}平均每个请求时间: 单个: {single_time:.4f}秒, 批量: {batch_time/max_batch:.4f}秒{reset}")
        
        speedup = (single_time*max_batch/batch_time) if batch_time > 0 else 0
        print(f"{yellow}性能提升: {speedup:.2f}倍{reset}")
        
        if batch_10000_success:
            print(f"{green}!!! 该节点支持10000个批量请求 !!!{reset}")
    else:
        print(f"{red}节点不支持批量请求{reset}")
    
    # 测试完一个节点后休息一下，避免被封
    time.sleep(2)
    
    return {
        "url": rpc_url,
        "single_request": single_success,
        "batch_request": batch_success,
        "batch_10000": batch_10000_success,
        "single_time": single_time,
        "batch_time": batch_time,
        "max_batch_size": max_batch,
        "speedup": (single_time*max_batch/batch_time) if batch_time > 0 else 0
    }

def main():
    # 加载RPC节点
    rpc_nodes = load_rpc_nodes("rpc.txt")
    
    if not rpc_nodes:
        print(f"{red}没有找到RPC节点，请检查rpc.txt文件{reset}")
        return
    
    print(f"{green}加载了 {len(rpc_nodes)} 个RPC节点{reset}")
    print(f"{yellow}开始测试RPC节点超大批量请求支持...{reset}")
    print(f"{red}警告: 测试10000批量请求可能会导致节点暂时封禁您的IP，请谨慎使用{reset}")
    
    # 确认继续
    confirm = input(f"{yellow}确认继续测试? (y/n): {reset}")
    if confirm.lower() != 'y':
        print(f"{red}测试已取消{reset}")
        return
    
    results = []
    
    # 使用较少的线程以减少负载
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(test_rpc_node, node): node for node in rpc_nodes}
        for future in futures:
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"{red}测试节点时出错: {e}{reset}")
    
    # 结果汇总
    batch_supported = [r for r in results if r["batch_request"]]
    batch_1000_supported = [r for r in results if r["max_batch_size"] >= 1000]
    batch_5000_supported = [r for r in results if r["max_batch_size"] >= 5000]
    batch_10000_supported = [r for r in results if r["batch_10000"]]
    
    # 按照支持的最大批量大小排序
    batch_supported.sort(key=lambda x: x["max_batch_size"], reverse=True)
    
    print(f"\n{'-'*80}")
    print(f"{cyan}测试完成! 结果汇总:{reset}")
    print(f"{green}测试节点总数: {len(results)}{reset}")
    print(f"{green}支持单个请求的节点数: {len([r for r in results if r['single_request']])}{reset}")
    print(f"{green}支持批量请求的节点数: {len(batch_supported)}{reset}")
    print(f"{green}支持1000+批量请求的节点数: {len(batch_1000_supported)}{reset}")
    print(f"{green}支持5000+批量请求的节点数: {len(batch_5000_supported)}{reset}")
    print(f"{green}支持10000批量请求的节点数: {len(batch_10000_supported)}{reset}")
    
    # 保存所有结果到JSON文件
    results_json = os.path.join(results_dir, "all_rpc_results.json")
    with open(results_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    
    # 为不同批量级别的节点创建文件
    if batch_supported:
        with open(os.path.join(results_dir, "batch_rpc_nodes.txt"), "w", encoding="utf-8") as f:
            for node in batch_supported:
                f.write(f"{node['url']} (最大批量: {node['max_batch_size']}, 性能提升: {node['speedup']:.2f}倍)\n")
    
    if batch_1000_supported:
        with open(os.path.join(results_dir, "batch_1000_rpc_nodes.txt"), "w", encoding="utf-8") as f:
            for node in batch_1000_supported:
                f.write(f"{node['url']} (最大批量: {node['max_batch_size']}, 性能提升: {node['speedup']:.2f}倍)\n")
    
    if batch_5000_supported:
        with open(os.path.join(results_dir, "batch_5000_rpc_nodes.txt"), "w", encoding="utf-8") as f:
            for node in batch_5000_supported:
                f.write(f"{node['url']} (最大批量: {node['max_batch_size']}, 性能提升: {node['speedup']:.2f}倍)\n")
    
    if batch_10000_supported:
        with open(os.path.join(results_dir, "batch_10000_rpc_nodes.txt"), "w", encoding="utf-8") as f:
            for node in batch_10000_supported:
                f.write(f"{node['url']} (性能提升: {node['speedup']:.2f}倍)\n")
        
        print(f"\n{yellow}支持10000批量请求的节点:{reset}")
        for node in batch_10000_supported:
            print(f"{green}- {node['url']} (性能提升: {node['speedup']:.2f}倍){reset}")
        
        print(f"\n{green}已将支持10000批量请求的节点保存到 {os.path.join(results_dir, 'batch_10000_rpc_nodes.txt')}{reset}")
    else:
        print(f"\n{red}没有找到支持10000批量请求的节点{reset}")
        
    print(f"\n{green}所有测试结果已保存到 {results_dir} 目录{reset}")

if __name__ == "__main__":
    main()