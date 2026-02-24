#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 启动 Quant Flow 容器...${NC}"

# 检查并修复 logs 目录权限
LOGS_DIR="/app/logs"

if [ -d "$LOGS_DIR" ]; then
    echo -e "${YELLOW}📁 检查 logs 目录权限...${NC}"

    # 检查当前用户是否可以写入
    if [ ! -w "$LOGS_DIR" ]; then
        echo -e "${YELLOW}⚠️  logs 目录权限不足，尝试修复...${NC}"

        # 尝试修复权限（这在某些情况下可能需要 root 权限）
        chmod -R u+w "$LOGS_DIR" 2>/dev/null || {
            echo -e "${RED}❌ 无法修复 logs 目录权限${NC}"
            echo -e "${YELLOW}💡 解决方案：${NC}"
            echo -e "   1. 停止容器: docker-compose down"
            echo -e "   2. 修复权限: chmod -R 777 ./logs"
            echo -e "   3. 重启容器: docker-compose up -d"
            exit 1
        }
    fi

    # 确保子目录也可写
    mkdir -p "$LOGS_DIR/decisions" "$LOGS_DIR/trades"
    chmod -R u+w "$LOGS_DIR/decisions" "$LOGS_DIR/trades" 2>/dev/null || true

    echo -e "${GREEN}✅ logs 目录权限检查完成${NC}"
else
    echo -e "${YELLOW}📁 创建 logs 目录...${NC}"
    mkdir -p "$LOGS_DIR/decisions" "$LOGS_DIR/trades"
    echo -e "${GREEN}✅ logs 目录创建完成${NC}"
fi

# 检查并修复 models 目录权限（QLib 模型存储）
MODELS_DIR="/app/models"
echo -e "${YELLOW}📁 检查 models 目录权限...${NC}"
mkdir -p "$MODELS_DIR/qlib" 2>/dev/null || true
if [ -d "$MODELS_DIR" ] && [ ! -w "$MODELS_DIR/qlib" ]; then
    echo -e "${YELLOW}⚠️  models/qlib 目录权限不足，尝试修复...${NC}"
    chmod -R u+w "$MODELS_DIR" 2>/dev/null || true
fi
echo -e "${GREEN}✅ models 目录权限检查完成${NC}"

# 验证必要的配置文件
echo -e "${YELLOW}📋 检查配置文件...${NC}"

if [ ! -f "/app/config.yaml" ]; then
    echo -e "${RED}❌ 配置文件 config.yaml 不存在${NC}"
    echo -e "${YELLOW}💡 请确保已挂载 config.yaml 文件${NC}"
    exit 1
fi

echo -e "${GREEN}✅ 配置文件检查完成${NC}"

# 启动应用程序
echo -e "${GREEN}🎯 启动应用程序...${NC}"
echo ""

exec python main.py
