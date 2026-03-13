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
    "/app/data"
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
find /app/data -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
find /app/experiments -not -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true

echo -e "${GREEN}✅ 目录权限修复完成${NC}"

# ========== 创建运行用户 ==========
# supervisord 以 root 运行，通过 user 指令降权子进程
# 回填模式通过 gosu 降权，也需要此用户
groupadd -g "$PGID" appgroup 2>/dev/null || true
useradd -u "$PUID" -g "$PGID" -M -s /bin/false -d /app appuser 2>/dev/null || true
export APP_USER=appuser

# ========== 解析运行模式 ==========
RUN_MODE=${RUN_MODE:-main}

case "$RUN_MODE" in
    main)
        ENABLE_MAIN=true
        ENABLE_GRID=false
        ;;
    grid)
        ENABLE_MAIN=false
        ENABLE_GRID=true
        ;;
    all)
        ENABLE_MAIN=true
        ENABLE_GRID=true
        ;;
    *)
        echo -e "${RED}❌ 无效的 RUN_MODE: ${RUN_MODE}，支持: main / grid / all${NC}"
        exit 1
        ;;
esac

export ENABLE_MAIN ENABLE_GRID
export MAIN_CONFIG=${MAIN_CONFIG:-config.yaml}
export GRID_CONFIG=${GRID_CONFIG:-config.grid.yaml}

echo -e "${YELLOW}📋 运行模式: ${RUN_MODE}${NC}"

# ========== 交易模式配置 ==========
echo -e "${YELLOW}📋 主交易配置: ${MAIN_CONFIG}, 网格配置: ${GRID_CONFIG}${NC}"

# ========== 验证配置文件 ==========
echo -e "${YELLOW}📋 检查配置文件...${NC}"

if [ "$ENABLE_MAIN" = "true" ] && [ ! -f "/app/${MAIN_CONFIG}" ]; then
    echo -e "${RED}❌ 主交易配置文件 ${MAIN_CONFIG} 不存在${NC}"
    echo -e "${YELLOW}💡 请确保已挂载 ${MAIN_CONFIG} 文件${NC}"
    exit 1
fi

if [ "$ENABLE_GRID" = "true" ] && [ ! -f "/app/${GRID_CONFIG}" ]; then
    echo -e "${RED}❌ 网格配置文件 ${GRID_CONFIG} 不存在${NC}"
    echo -e "${YELLOW}💡 请确保已挂载 ${GRID_CONFIG} 文件${NC}"
    exit 1
fi

echo -e "${GREEN}✅ 配置文件检查完成${NC}"

# ========== 启动 supervisord ==========
# supervisord 以 root 身份运行（PID 1），子进程通过 user 指令降权
echo -e "${GREEN}🎯 启动应用程序 (模式: ${RUN_MODE}, 子进程 UID=${PUID}, GID=${PGID})...${NC}"
echo ""

exec supervisord -c /app/supervisord.conf
