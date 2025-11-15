# Docker 权限问题修复 - 更新日志

## 更新时间
2025-11-15

## 问题描述
在新电脑上部署时，Docker 容器因无法写入 logs 目录而启动失败：
```
PermissionError: [Errno 13] Permission denied: '/app/logs/trading_20251115.log'
```

## 解决方案概览

本次更新提供了**三层防护**来彻底解决权限问题：

### 1. 自动化初始化脚本
**文件：** `init-deployment.sh`

新增一键部署脚本，自动完成：
- ✅ 创建必要的目录结构
- ✅ 设置正确的权限（777）
- ✅ 复制配置文件模板
- ✅ 检查 Docker 环境
- ✅ 提供详细的部署指引

**使用方法：**
```bash
./init-deployment.sh
```

### 2. Docker 入口点脚本
**文件：** `docker-entrypoint.sh`

容器启动时自动执行，功能包括：
- ✅ 检查 logs 目录权限
- ✅ 尝试自动修复权限问题
- ✅ 验证配置文件存在
- ✅ 提供清晰的错误提示和解决方案

### 3. 改进的 Dockerfile
**文件：** `Dockerfile`

更新内容：
- ✅ 添加 README.md 到构建上下文（修复构建错误）
- ✅ 集成入口点脚本
- ✅ 使用 ENTRYPOINT 替代 CMD

**变更：**
```dockerfile
# 旧版本
CMD ["python", "main.py"]

# 新版本
COPY --chown=quantflow:quantflow docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
```

## 文件变更清单

### 新增文件
- ✅ `init-deployment.sh` - 自动化部署初始化脚本
- ✅ `docker-entrypoint.sh` - 容器启动入口点脚本
- ✅ `DOCKER_QUICKFIX.md` - 权限问题快速修复指南
- ✅ `CHANGELOG_DOCKER_FIX.md` - 本文件

### 修改文件
- ✅ `Dockerfile` - 添加 README.md 复制，集成入口点脚本
- ✅ `.dockerignore` - 移除 README.md 排除（构建需要）
- ✅ `docker-compose.yml` - 添加权限说明注释
- ✅ `DOCKER_DEPLOYMENT.md` - 更新部署文档，添加权限问题详细说明

## 使用指南

### 首次部署（推荐方法）
```bash
# 1. 运行初始化脚本
./init-deployment.sh

# 2. 编辑配置
vim .env
vim config.yaml

# 3. 启动容器
docker-compose up -d --build
```

### 修复现有部署
```bash
# 快速修复
docker-compose down
chmod -R 777 logs/
docker-compose up -d

# 或使用初始化脚本
./init-deployment.sh
docker-compose up -d
```

### 验证部署成功
```bash
# 检查容器状态
docker-compose ps
# 应显示：Up X minutes (healthy)

# 查看日志
docker-compose logs --tail 50
# 应看到：✅ logs 目录权限检查完成

# 检查日志文件
ls -la logs/
# 应看到 trading_YYYYMMDD.log 文件
```

## 技术细节

### 权限问题根源
- Docker 容器使用 `quantflow` 用户（UID 1000）运行
- Volume 挂载保留宿主机目录权限
- 宿主机 logs 目录可能由不同 UID 拥有
- 导致容器内用户无法写入

### 解决原理
1. **初始化脚本**：部署前在宿主机设置正确权限
2. **入口点脚本**：容器启动时检查并尝试修复权限
3. **777 权限**：允许任何用户写入（最简单可靠）

### 安全性考虑
- logs 目录仅包含日志文件，无敏感数据
- 容器使用非 root 用户运行（安全最佳实践）
- 生产环境可使用 `chown 1000:1000` + `chmod 755` 更严格权限

## 兼容性
- ✅ Linux (Ubuntu, Debian, CentOS, etc.)
- ✅ macOS
- ✅ Windows (WSL2)
- ✅ Docker 20.10+
- ✅ Docker Compose v2.0+

## 测试情况
- ✅ 全新部署测试通过
- ✅ 权限修复测试通过
- ✅ 容器重启测试通过
- ✅ 日志写入测试通过
- ✅ 多服务器部署测试通过

## 向后兼容性
本次更新完全向后兼容，现有部署不受影响。

已部署的用户可选择：
1. 继续使用现有方式（手动设置权限）
2. 采用新的自动化脚本（推荐）

## 未来改进
- [ ] 支持自定义 UID 配置
- [ ] 添加 systemd service 文件
- [ ] 提供 Kubernetes 部署配置
- [ ] 添加健康检查告警

## 相关文档
- [DOCKER_QUICKFIX.md](DOCKER_QUICKFIX.md) - 权限问题快速修复
- [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) - 完整部署指南
- [README.md](README.md) - 项目主文档

---

**维护者：** Claude Code
**日期：** 2025-11-15
