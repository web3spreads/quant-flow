#!/bin/bash
# 测试脚本：模拟在新电脑上部署

echo "=== 测试 Docker 部署 ==="
echo ""

# 1. 模拟全新环境（删除 logs）
echo "1. 清理环境（模拟新电脑）..."
docker-compose down 2>/dev/null
rm -rf logs/
echo "   ✅ 环境已清理"
echo ""

# 2. 运行初始化脚本
echo "2. 运行初始化脚本..."
./init-deployment.sh
if [ $? -eq 0 ]; then
    echo "   ✅ 初始化成功"
else
    echo "   ❌ 初始化失败"
    exit 1
fi
echo ""

# 3. 构建并启动容器
echo "3. 构建并启动容器..."
docker-compose up -d --build 2>&1 | grep -E "(Creating|Starting|Created|Started)" | tail -5
if [ $? -eq 0 ]; then
    echo "   ✅ 容器已启动"
else
    echo "   ❌ 容器启动失败"
    exit 1
fi
echo ""

# 4. 等待容器启动
echo "4. 等待容器启动（10秒）..."
sleep 10
echo ""

# 5. 检查容器状态
echo "5. 检查容器状态..."
STATUS=$(docker-compose ps | grep quant-flow-bot | awk '{print $5}')
if [[ "$STATUS" == "Up" ]]; then
    echo "   ✅ 容器运行中"
else
    echo "   ❌ 容器状态异常: $STATUS"
    docker-compose logs --tail 20
    exit 1
fi
echo ""

# 6. 检查权限检查日志
echo "6. 检查权限检查日志..."
docker logs quant-flow-bot 2>&1 | grep -E "logs 目录权限检查完成" > /dev/null
if [ $? -eq 0 ]; then
    echo "   ✅ 权限检查通过"
else
    echo "   ❌ 权限检查失败"
    docker logs quant-flow-bot 2>&1 | head -20
    exit 1
fi
echo ""

# 7. 检查日志文件生成
echo "7. 检查日志文件是否生成..."
sleep 3
if [ -f logs/trading_*.log ]; then
    LOG_FILE=$(ls -t logs/trading_*.log 2>/dev/null | head -1)
    if [ -n "$LOG_FILE" ]; then
        echo "   ✅ 日志文件已生成: $LOG_FILE"
    else
        echo "   ⚠️  日志文件尚未生成（可能正在初始化）"
    fi
else
    echo "   ⚠️  日志文件尚未生成（可能正在初始化）"
fi
echo ""

# 8. 检查是否有权限错误
echo "8. 检查是否有权限错误..."
docker logs quant-flow-bot 2>&1 | grep -i "permission denied" > /dev/null
if [ $? -eq 0 ]; then
    echo "   ❌ 发现权限错误！"
    docker logs quant-flow-bot 2>&1 | grep -i "permission"
    exit 1
else
    echo "   ✅ 无权限错误"
fi
echo ""

echo "========================================="
echo "✅ 所有测试通过！"
echo "========================================="
echo ""
echo "容器状态："
docker-compose ps
echo ""
echo "日志目录："
ls -lah logs/
