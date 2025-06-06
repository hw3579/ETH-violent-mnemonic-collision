import ctypes
import os

# 加载共享库
if os.name == 'nt':
    _lib_path = os.path.join(os.path.dirname(__file__), './out/build/x64-Release/ethkeygen.dll')
elif os.name == 'posix':
    _lib_path = os.path.join(os.path.dirname(__file__), './build/libethkeygen.so')
_lib = ctypes.CDLL(_lib_path)

# 定义函数签名
_lib.generate_eth_key.argtypes = [
    ctypes.c_char_p,  # mnemonic
    ctypes.c_char_p,  # path
    ctypes.c_char_p,  # private_key_out
    ctypes.c_char_p   # address_out
]
_lib.generate_eth_key.restype = ctypes.c_void_p

def generate_eth_address_from_mnemonic(mnemonic):
    """使用C++实现的高性能版本生成以太坊地址和私钥"""
    account_path = "m/44'/60'/0'/0/0"
    
    # 准备输出缓冲区
    private_key_buf = ctypes.create_string_buffer(65)  # 32字节的十六进制表示 + null终止符
    address_buf = ctypes.create_string_buffer(43)      # "0x" + 40字节的十六进制表示 + null终止符
    
    # 调用C++函数
    error = _lib.generate_eth_key(
        mnemonic.encode('utf-8'),
        account_path.encode('utf-8'),
        private_key_buf,
        address_buf
    )
    
    if error:
        error_msg = ctypes.string_at(error).decode('utf-8')
        raise ValueError(f"错误生成以太坊密钥: {error_msg}")
    
    # 转换结果
    private_key = bytes.fromhex(private_key_buf.value.decode('utf-8'))
    eth_address = address_buf.value.decode('utf-8')
    
    return eth_address, private_key