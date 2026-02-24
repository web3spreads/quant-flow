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

# 自动探测当前用户 UID/GID 并写入 .env
echo -e "${YELLOW}📁 配置运行用户 UID/GID...${NC}"

CURRENT_UID=$(id -u)
CURRENT_GID=$(id -g)

# 将 PUID/PGID 写入 .env（如果尚未设置）
if [ -f ".env" ]; then
    # 移除已有的 PUID/PGID 行（避免重复）
    sed -i '/^PUID=/d' .env
    sed -i '/^PGID=/d' .env
fi

echo "PUID=${CURRENT_UID}" >> .env
echo "PGID=${CURRENT_GID}" >> .env

echo -e "${GREEN}✅ 已写入 PUID=${CURRENT_UID}, PGID=${CURRENT_GID} 到 .env${NC}"
echo -e "   容器内运行用户将匹配当前宿主机用户，挂载卷权限自动兼容"

echo ""

# 创建 logs 目录结构（无需 777 权限，容器 entrypoint 会自动 chown）
echo -e "${YELLOW}📁 创建目录结构...${NC}"

mkdir -p logs/decisions logs/trades models/qlib data/qlib data/market_info experiments

echo -e "${GREEN}✅ 目录结构已创建${NC}"
echo -e "${BLUE}   logs/decisions/     - 决策日志${NC}"
echo -e "${BLUE}   logs/trades/        - 交易日志${NC}"
echo -e "${BLUE}   models/qlib/        - QLib 模型${NC}"
echo -e "${BLUE}   data/qlib/          - QLib 数据缓存${NC}"
echo -e "${BLUE}   data/market_info/   - 市场信息存储${NC}"
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
echo -e "   - PUID/PGID 已自动配置为当前用户 (${CURRENT_UID}:${CURRENT_GID})"
echo -e "   - 容器启动时会自动匹配宿主机用户权限，无需手动 chmod 777"
echo -e "   - 如果更换部署用户，重新运行此脚本即可"
echo ""
