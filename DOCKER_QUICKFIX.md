# Docker 权限问题快速修复指南

## 问题描述

在新电脑上部署 Quant Flow 时，容器启动失败，日志显示权限错误：

```
❌ 启动失败: [Errno 13] Permission denied: '/app/logs/trading_20251115.log'

Traceback (most recent call last):
  File "/app/main.py", line 633, in main
    bot = QuantFlowBot(config_path="config.yaml")
    ...
PermissionError: [Errno 13] Permission denied: '/app/logs/trading_20251115.log'
```

## 问题原因

Docker 容器内使用非 root 用户 `quantflow`（UID 1000）运行以提高安全性。当 Docker volume 挂载宿主机的 `logs/` 目录时，如果该目录权限不正确，容器内的用户无法写入日志文件。

## 解决方案

### ⭐ 方法 1：一键修复（推荐）

```bash
# 停止容器
docker-compose down

# 运行初始化脚本（自动修复所有问题）
./init-deployment.sh

# 重新启动
docker-compose up -d --build
```

### 方法 2：手动快速修复

```bash
# 1. 停止容器
docker-compose down

# 2. 修复权限
chmod -R 777 logs/

# 3. 重新启动
docker-compose up -d
```

### 方法 3：完全重建

```bash
# 1. 停止并删除容器
docker-compose down

# 2. 删除旧日志目录
rm -rf logs/

# 3. 重新创建目录并设置权限
mkdir -p logs/decisions logs/trades
chmod -R 777 logs/

# 4. 重新启动
docker-compose up -d --build
```

## 验证修复

```bash
# 1. 检查容器状态（应该显示 "Up" 而不是 "Restarting"）
docker-compose ps

# 输出示例：
# NAME              STATUS
# quant-flow-bot    Up 2 minutes (healthy)

# 2. 查看日志（不应该有权限错误）
docker-compose logs --tail 50

# 应该看到正常的启动信息：
# 🚀 启动 Quant Flow 容器...
# ✅ logs 目录权限检查完成
# ✅ 配置文件检查完成
# 🎯 启动应用程序...

# 3. 检查日志文件是否生成
ls -la logs/

# 应该看到：
# -rwxrwxrwx  1 user user  2906 Nov 15 10:04 trading_20251115.log
```

## 技术细节

### 为什么需要 777 权限？

在 Docker 环境中：
- 容器内运行用户：`quantflow` (UID 1000)
- 宿主机目录所有者：可能是不同的 UID
- Volume 挂载保留宿主机权限

设置 777 权限允许任何用户写入，确保容器内的 `quantflow` 用户可以创建日志文件。

**生产环境建议：** 如果安全性要求更高，可以：
```bash
# 将 logs 目录所有权改为 UID 1000
sudo chown -R 1000:1000 logs/
chmod -R 755 logs/
```

### 自动化解决方案

本项目提供了三层自动化保护：

1. **初始化脚本** (`init-deployment.sh`)
   - 部署前自动设置正确权限
   - 检查所有必要配置

2. **Docker 入口点** (`docker-entrypoint.sh`)
   - 容器启动时自动检查权限
   - 尝试自动修复（如果可能）
   - 提供清晰的错误提示

3. **文档说明**
   - 详细的故障排查指南
   - 多种解决方案供选择

## 预防措施

### 首次部署新服务器

**推荐流程：**

```bash
# 1. 克隆代码
git clone <your-repo>
cd quant-flow

# 2. 运行初始化脚本（自动处理所有配置）
./init-deployment.sh

# 3. 编辑配置
vim .env
vim config.yaml

# 4. 启动
docker-compose up -d --build
```

### 更新部署

```bash
# 1. 拉取最新代码
git pull

# 2. 确保权限正确（如果之前设置过可跳过）
chmod -R 777 logs/

# 3. 重新构建并启动
docker-compose up -d --build
```

## 常见问题

### Q: 为什么使用 777 权限？不是不安全吗？

A: 在 Docker 环境中，这是最简单可靠的解决方案。logs 目录只包含日志文件，不包含敏感信息。如果需要更高安全性，可以使用 `chown 1000:1000` + `chmod 755`。

### Q: 每次重启都需要运行 init-deployment.sh 吗？

A: 不需要。只需要在以下情况运行：
- 首次部署到新服务器
- 删除了 logs 目录后
- 遇到权限问题时

### Q: 我已经设置了权限，为什么还是报错？

A: 请检查：
1. logs 目录确实存在：`ls -la logs/`
2. 权限确实是 777：应该显示 `drwxrwxrwx`
3. 子目录也有权限：`ls -la logs/decisions logs/trades`
4. 容器已经重启：`docker-compose restart`

### Q: 生产环境有更安全的方案吗？

A: 有！使用精确的用户权限：

```bash
# 方案 1：匹配 UID
sudo chown -R 1000:1000 logs/
chmod -R 755 logs/

# 方案 2：使用 ACL（如果系统支持）
setfacl -R -m u:1000:rwx logs/
setfacl -R -d -m u:1000:rwx logs/

# 方案 3：在 Dockerfile 中使用宿主机 UID
# 构建时传入参数：
docker-compose build --build-arg USER_ID=$(id -u)
```

## 更多信息

详细的 Docker 部署文档请参考：
- [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) - 完整部署指南
- [README.md](README.md) - 项目主文档
- [QUICKSTART.md](QUICKSTART.md) - 快速开始指南

## 需要帮助？

如果以上方案都无法解决问题：

1. 查看完整日志：`docker-compose logs --tail 200`
2. 检查系统日志：`journalctl -u docker`
3. 提交 Issue 并附上：
   - 错误日志
   - `ls -la logs/` 输出
   - `docker-compose ps` 输出
   - 系统信息：`uname -a`

---

**最后更新：** 2025-11-15
