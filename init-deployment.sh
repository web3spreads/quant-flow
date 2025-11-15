#!/bin/bash
# Quant Flow 部署初始化脚本
# 用于首次部署或重新部署时设置正确的目录权限

set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Quant Flow 部署初始化${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 检查必要的配置文件
echo -e "${YELLOW}📋 检查配置文件...${NC}"

if [ ! -f "config.yaml" ]; then
    if [ -f "config.yaml.example" ]; then
        echo -e "${YELLOW}⚠️  config.yaml 不存在，从示例文件复制...${NC}"
        cp config.yaml.example config.yaml
        echo -e "${GREEN}✅ 已创建 config.yaml，请编辑此文件配置您的参数${NC}"
    else
        echo -e "${RED}❌ config.yaml.example 不存在${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✅ config.yaml 已存在${NC}"
fi

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo -e "${YELLOW}⚠️  .env 不存在，从示例文件复制...${NC}"
        cp .env.example .env
        echo -e "${GREEN}✅ 已创建 .env，请编辑此文件配置您的 API 密钥${NC}"
    else
        echo -e "${YELLOW}⚠️  .env.example 不存在，跳过${NC}"
    fi
else
    echo -e "${GREEN}✅ .env 已存在${NC}"
fi

echo ""

# 创建并设置 logs 目录权限
echo -e "${YELLOW}📁 设置 logs 目录权限...${NC}"

# 创建 logs 目录结构
mkdir -p logs/decisions
mkdir -p logs/trades

# 设置权限 (777 确保容器内的 quantflow 用户可以写入)
# 注意：这是为了 Docker volume 挂载的兼容性
chmod -R 777 logs/

echo -e "${GREEN}✅ logs 目录权限已设置 (777)${NC}"
echo -e "${BLUE}   目录结构：${NC}"
echo -e "   logs/"
echo -e "   ├── decisions/"
echo -e "   └── trades/"
echo ""

# 检查 Docker 和 Docker Compose
echo -e "${YELLOW}🐳 检查 Docker 环境...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker 未安装${NC}"
    echo -e "${YELLOW}💡 请先安装 Docker: https://docs.docker.com/get-docker/${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose 未安装${NC}"
    echo -e "${YELLOW}💡 请先安装 Docker Compose${NC}"
    exit 1
fi

# 检查 Docker daemon 是否运行
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker daemon 未运行${NC}"
    echo -e "${YELLOW}💡 请启动 Docker 服务${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker 环境检查通过${NC}"
echo ""

# 提示下一步操作
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✅ 初始化完成！${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}📝 下一步操作：${NC}"
echo -e "   1. 编辑 ${BLUE}.env${NC} 文件，配置 API 密钥"
echo -e "   2. 编辑 ${BLUE}config.yaml${NC} 文件，配置交易参数"
echo -e "   3. 构建并启动容器："
echo -e "      ${GREEN}docker-compose up -d --build${NC}"
echo -e "   4. 查看日志："
echo -e "      ${GREEN}docker-compose logs -f${NC}"
echo -e "   5. 停止容器："
echo -e "      ${GREEN}docker-compose down${NC}"
echo ""
echo -e "${YELLOW}💡 提示：${NC}"
echo -e "   - logs 目录已设置为 777 权限，允许容器写入日志"
echo -e "   - 如果遇到权限问题，重新运行此脚本即可修复"
echo ""
