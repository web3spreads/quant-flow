#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_USER="quantflow"
APP_GROUP="quantflow"

# 需要写入权限的目录列表
WRITABLE_DIRS=(
    "/app/logs"
    "/app/logs/decisions"
    "/app/logs/trades"
    "/app/models"
    "/app/models/qlib"
    "/app/data/qlib"
    "/app/experiments"
)

echo -e "${GREEN}🚀 启动 Quant Flow 容器...${NC}"

# ========== 动态 UID/GID 匹配 ==========
# 读取环境变量，默认 1000
PUID=${PUID:-1000}
PGID=${PGID:-1000}

CUR_UID=$(id -u "$APP_USER")
CUR_GID=$(getent group "$APP_GROUP" | cut -d: -f3)

echo -e "${YELLOW}📋 运行用户: ${APP_USER} (目标 UID=${PUID}, GID=${PGID}; 当前 UID=${CUR_UID}, GID=${CUR_GID})${NC}"

# 如果 GID 不匹配，修改组 ID
if [ "$PGID" != "$CUR_GID" ]; then
    echo -e "${YELLOW}🔧 调整 GID: ${CUR_GID} -> ${PGID}${NC}"
    groupmod -o -g "$PGID" "$APP_GROUP"
fi

# 如果 UID 不匹配，修改用户 ID
if [ "$PUID" != "$CUR_UID" ]; then
    echo -e "${YELLOW}🔧 调整 UID: ${CUR_UID} -> ${PUID}${NC}"
    usermod -o -u "$PUID" "$APP_USER"
fi

# ========== 修复可写目录权限 ==========
echo -e "${YELLOW}📁 修复可写目录权限...${NC}"

for dir in "${WRITABLE_DIRS[@]}"; do
    mkdir -p "$dir"
    chown "$PUID:$PGID" "$dir"
done

# 修复已有日志文件的归属（仅修改属主不匹配的文件，避免大量无效操作）
find /app/logs -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/models -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/data/qlib -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
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
echo -e "${GREEN}🎯 以 ${APP_USER}(UID=${PUID}, GID=${PGID}) 启动应用程序...${NC}"
echo ""

exec gosu "$APP_USER" python main.py
