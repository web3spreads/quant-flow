#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 需要写入权限的目录列表
WRITABLE_DIRS=(
    "/app/logs"
    "/app/logs/decisions"
    "/app/logs/trades"
    "/app/models"
    "/app/models/qlib"
    "/app/data"
    "/app/data/qlib"
    "/app/data/market_info"
    "/app/experiments"
)

echo -e "${GREEN}🚀 启动 Quant Flow 容器...${NC}"

# ========== 读取目标 UID/GID ==========
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo -e "${YELLOW}📋 运行用户: UID=${PUID}, GID=${PGID}${NC}"

# ========== 修复可写目录权限 ==========
echo -e "${YELLOW}📁 修复可写目录权限...${NC}"

for dir in "${WRITABLE_DIRS[@]}"; do
    mkdir -p "$dir"
    chown "$PUID:$PGID" "$dir"
done

# 修复已有文件的归属（仅修改属主不匹配的文件，避免大量无效操作）
find /app/logs -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/models -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/data -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/experiments -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true

echo -e "${GREEN}✅ 目录权限修复完成${NC}"

# ========== 验证配置文件 ==========
echo -e "${YELLOW}📋 检查配置文件...${NC}"

if [ ! -f "/app/config.yaml" ]; then
    echo -e "${RED}❌ 配置文件 config.yaml 不存在${NC}"
    echo -e "${YELLOW}💡 请确保已挂载 config.yaml 文件${NC}"
    exit 1
fi

echo -e "${GREEN}✅ 配置文件检查完成${NC}"

# ========== 降权启动应用 ==========
# 直接使用数字 UID:GID 降权，不依赖容器内用户名
echo -e "${GREEN}🎯 以 UID=${PUID}, GID=${PGID} 启动应用程序...${NC}"
echo ""

exec gosu "${PUID}:${PGID}" python main.py
