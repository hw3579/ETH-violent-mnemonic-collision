cmake_minimum_required(VERSION 3.10)
project(eth_key_generator CXX)

# 设置C++标准
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 设置构建类型
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# 启用位置无关代码
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Windows特定设置
if(WIN32)
  add_definitions(-D_WIN32)
  # 确保导出符号
  set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
endif()

# 查找OpenSSL
find_package(OpenSSL REQUIRED)
if(NOT OpenSSL_FOUND)
  message(FATAL_ERROR "OpenSSL not found")
endif()

# 包含头文件目录
include_directories(${OPENSSL_INCLUDE_DIR})

# 添加源文件
set(SOURCES src/eth_key_generator.cpp)

# 创建共享库
add_library(ethkeygen SHARED ${SOURCES})

# 链接OpenSSL库
target_link_libraries(ethkeygen ${OPENSSL_CRYPTO_LIBRARY})

# 设置输出名称
if(WIN32)
  set_target_properties(ethkeygen PROPERTIES
    OUTPUT_NAME "ethkeygen"
    PREFIX ""  # Windows通常不使用lib前缀
  )
else()
  set_target_properties(ethkeygen PROPERTIES
    OUTPUT_NAME "ethkeygen"
    PREFIX "lib"
  )
endif()

# 安装规则
install(TARGETS ethkeygen
  RUNTIME DESTINATION bin
  LIBRARY DESTINATION lib
)