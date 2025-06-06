#include <iostream>
#include <vector>
#include <string>
#include <sstream>
#include <iomanip>
#include <openssl/sha.h>
#include <openssl/hmac.h>
#include <openssl/evp.h>
#include <openssl/bn.h>
#include <openssl/ec.h>
#include <cstring>

#include <openssl/opensslv.h>
#ifndef OPENSSL_VERSION_NUMBER
#define OPENSSL_VERSION_NUMBER 0
#endif


// 自定义实现，解决找不到BN_bn2bin_padded的问题
static int my_BN_bn2bin_padded(unsigned char *to, int tolen, const BIGNUM *bn)
{
    int size = BN_num_bytes(bn);
    if (size > tolen) {
        return 0;  // 错误：缓冲区太小
    }
    
    // 填充前导零
    memset(to, 0, tolen - size);
    
    // 将BIGNUM转换为二进制，放在缓冲区末尾
    BN_bn2bin(bn, to + tolen - size);
    
    return 1;  // 成功
}

// 定义宏，用我们的实现替换所有BN_bn2bin_padded调用
#define BN_bn2bin_padded my_BN_bn2bin_padded



// 将助记词转换为种子
std::vector<uint8_t> mnemonic_to_seed(const std::string& mnemonic, const std::string& passphrase = "")
{
    std::string salt = "mnemonic" + passphrase;
    std::vector<uint8_t> seed(64); // 512位种子
    
    // 使用PBKDF2-HMAC-SHA512，2048轮迭代
    PKCS5_PBKDF2_HMAC(
        mnemonic.c_str(), mnemonic.length(),
        reinterpret_cast<const unsigned char*>(salt.c_str()), salt.length(),
        2048,
        EVP_sha512(),
        64, seed.data()
    );
    
    return seed;
}

// 解析BIP-32路径
std::vector<uint32_t> parse_derivation_path(const std::string& path)
{
    std::vector<uint32_t> result;
    std::stringstream ss(path);
    std::string item;
    
    // 跳过开头的"m/"
    std::getline(ss, item, '/');
    
    while (std::getline(ss, item, '/')) {
        if (item.back() == '\'') {
            // 硬化路径，添加0x80000000
            item.pop_back();
            result.push_back(0x80000000 + std::stoul(item));
        } else {
            // 普通路径
            result.push_back(std::stoul(item));
        }
    }
    
    return result;
}

// HMAC-SHA512函数
std::vector<uint8_t> hmac_sha512(const std::vector<uint8_t>& key, const std::vector<uint8_t>& data)
{
    std::vector<uint8_t> digest(64); // 512位输出
    
    unsigned int len = 64;
    HMAC(
        EVP_sha512(),
        key.data(), key.size(),
        data.data(), data.size(),
        digest.data(), &len
    );
    
    return digest;
}

// 派生子密钥 (BIP-32)
std::pair<std::vector<uint8_t>, std::vector<uint8_t>> derive_child_key(
    const std::vector<uint8_t>& parent_key,
    const std::vector<uint8_t>& parent_chain_code,
    uint32_t index)
{
    std::vector<uint8_t> data;
    
    if (index & 0x80000000) {
        // 硬化派生 - 使用父私钥
        data.push_back(0x00);
        data.insert(data.end(), parent_key.begin(), parent_key.end());
    } else {
        // 非硬化派生 - 使用父公钥
        // 从私钥计算公钥
        EC_KEY* ec_key = EC_KEY_new_by_curve_name(NID_secp256k1);
        BIGNUM* priv_bn = BN_bin2bn(parent_key.data(), parent_key.size(), NULL);
        EC_KEY_set_private_key(ec_key, priv_bn);
        
        const EC_GROUP* group = EC_KEY_get0_group(ec_key);
        EC_POINT* pub_point = EC_POINT_new(group);
        EC_POINT_mul(group, pub_point, priv_bn, NULL, NULL, NULL);
        
        // 将公钥转换为未压缩格式 (33字节压缩或65字节未压缩)
        size_t pub_len = EC_POINT_point2oct(group, pub_point, POINT_CONVERSION_COMPRESSED, NULL, 0, NULL);
        std::vector<uint8_t> pub_key(pub_len);
        EC_POINT_point2oct(group, pub_point, POINT_CONVERSION_COMPRESSED, pub_key.data(), pub_len, NULL);
        
        // 添加公钥到数据
        data.insert(data.end(), pub_key.begin(), pub_key.end());
        
        // 释放资源
        EC_POINT_free(pub_point);
        BN_free(priv_bn);
        EC_KEY_free(ec_key);
    }
    
    // 添加索引 (大端序)
    data.push_back((index >> 24) & 0xFF);
    data.push_back((index >> 16) & 0xFF);
    data.push_back((index >> 8) & 0xFF);
    data.push_back(index & 0xFF);
    
    // 使用HMAC-SHA512派生子密钥
    std::vector<uint8_t> hmac_result = hmac_sha512(parent_chain_code, data);
    
    // 前32字节为子密钥
    std::vector<uint8_t> child_key_part(hmac_result.begin(), hmac_result.begin() + 32);
    // 后32字节为子链码
    std::vector<uint8_t> child_chain_code(hmac_result.begin() + 32, hmac_result.end());
    
    // 将子密钥与父密钥相加 (mod n)
    // 获取secp256k1曲线的阶
    BIGNUM* order = BN_new();
    BIGNUM* child_key_bn = BN_new();
    BIGNUM* parent_key_bn = BN_new();
    BIGNUM* sum = BN_new();
    BN_CTX* ctx = BN_CTX_new();
    
    EC_GROUP* group = EC_GROUP_new_by_curve_name(NID_secp256k1);
    EC_GROUP_get_order(group, order, ctx);
    
    // 转换为BIGNUM
    BN_bin2bn(child_key_part.data(), child_key_part.size(), child_key_bn);
    BN_bin2bn(parent_key.data(), parent_key.size(), parent_key_bn);
    
    // 相加并取模
    BN_mod_add(sum, child_key_bn, parent_key_bn, order, ctx);
    
    // 转回字节数组
    std::vector<uint8_t> child_key(32);
    // BN_bn2bin_padded(child_key.data(), 32, sum);
    // 替换为：
    if (my_BN_bn2bin_padded(child_key.data(), 32, sum) != 1) {
        // 处理错误，例如：
        std::fill(child_key.begin(), child_key.end(), 0);
        std::cerr << "Error: Failed to convert BIGNUM to byte array" << std::endl;
    }
    
    // 释放资源
    BN_free(order);
    BN_free(child_key_bn);
    BN_free(parent_key_bn);
    BN_free(sum);
    BN_CTX_free(ctx);
    EC_GROUP_free(group);
    
    return {child_key, child_chain_code};
}
// Keccak-256哈希
std::vector<uint8_t> keccak256(const std::vector<uint8_t>& data)
{
    // 这里使用OpenSSL的EVP接口，但在实际实现中可能需要使用专门的Keccak库
    std::vector<uint8_t> hash(32);
    
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(ctx, EVP_sha3_256(), NULL);
    EVP_DigestUpdate(ctx, data.data(), data.size());
    EVP_DigestFinal_ex(ctx, hash.data(), NULL);
    EVP_MD_CTX_free(ctx);
    
    return hash;
}

// 从私钥生成以太坊地址
std::string private_key_to_eth_address(const std::vector<uint8_t>& private_key)
{
    // 使用secp256k1曲线将私钥转换为公钥
    EC_KEY* key = EC_KEY_new_by_curve_name(NID_secp256k1);
    BIGNUM* bn = BN_bin2bn(private_key.data(), private_key.size(), NULL);
    EC_KEY_set_private_key(key, bn);
    
    const EC_GROUP* group = EC_KEY_get0_group(key);
    EC_POINT* pub_key = EC_POINT_new(group);
    EC_POINT_mul(group, pub_key, bn, NULL, NULL, NULL);
    EC_KEY_set_public_key(key, pub_key);
    
    // 将公钥转换为未压缩格式
    size_t pub_key_size = EC_POINT_point2oct(group, pub_key, POINT_CONVERSION_UNCOMPRESSED, NULL, 0, NULL);
    std::vector<uint8_t> pub_key_bytes(pub_key_size);
    EC_POINT_point2oct(group, pub_key, POINT_CONVERSION_UNCOMPRESSED, pub_key_bytes.data(), pub_key_size, NULL);
    
    // 释放资源
    EC_POINT_free(pub_key);
    BN_free(bn);
    EC_KEY_free(key);
    
    // 去掉前缀(0x04)后进行Keccak-256哈希
    std::vector<uint8_t> pub_key_without_prefix(pub_key_bytes.begin() + 1, pub_key_bytes.end());
    std::vector<uint8_t> hash = keccak256(pub_key_without_prefix);
    
    // 取最后20字节作为地址
    std::vector<uint8_t> address(hash.end() - 20, hash.end());
    
    // 转换为十六进制字符串
    std::stringstream ss;
    ss << "0x";
    for (auto byte : address) {
        ss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    }
    
    return ss.str();
}

// 主函数: 从助记词通过路径导出以太坊私钥和地址
std::pair<std::vector<uint8_t>, std::string> derive_eth_key_from_mnemonic(
    const std::string& mnemonic,
    const std::string& path = "m/44'/60'/0'/0/0")
{
    // 从助记词生成种子
    std::vector<uint8_t> seed = mnemonic_to_seed(mnemonic);
    
    // 生成主密钥和链码
    std::vector<uint8_t> hmac_result = hmac_sha512(
        std::vector<uint8_t>(reinterpret_cast<const uint8_t*>("Bitcoin seed"), 
                              reinterpret_cast<const uint8_t*>("Bitcoin seed") + 12),
        seed
    );
    
    std::vector<uint8_t> key(hmac_result.begin(), hmac_result.begin() + 32);
    std::vector<uint8_t> chain_code(hmac_result.begin() + 32, hmac_result.end());
    
    // 解析路径并导出子密钥
    std::vector<uint32_t> indices = parse_derivation_path(path);
    for (uint32_t index : indices) {
        auto [child_key, child_chain] = derive_child_key(key, chain_code, index);
        key = child_key;
        chain_code = child_chain;
    }
    
    // 计算以太坊地址
    std::string address = private_key_to_eth_address(key);
    
    return {key, address};
}

#ifdef _WIN32
    #define EXPORT __declspec(dllexport)
#else
    #define EXPORT
#endif

// 以下是Python接口函数
extern "C" {
    // 从助记词生成以太坊私钥和地址
    void* generate_eth_key(const char* mnemonic, const char* path, 
                          char* private_key_out, char* address_out) {
        try {
            auto [priv_key, addr] = derive_eth_key_from_mnemonic(mnemonic, path);
            
            // 转换私钥为十六进制字符串
            std::stringstream ss;
            for (auto byte : priv_key) {
                ss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
            }
            std::string priv_key_hex = ss.str();
            
            // 复制结果到输出缓冲区
            strcpy(private_key_out, priv_key_hex.c_str());
            strcpy(address_out, addr.c_str());
            
            return nullptr; // 成功
        } catch (const std::exception& e) {
            return const_cast<char*>(e.what()); // 返回错误信息
        }
    }
}