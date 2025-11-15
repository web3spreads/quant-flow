# Docker Deployment Guide

This guide explains how to deploy the Quant Flow trading bot using Docker and Docker Compose.

## Prerequisites

- Docker Engine 20.10 or later
- Docker Compose 2.0 or later
- At least 1GB of available RAM
- Stable internet connection for API access

## Quick Start

### 方法一：自动化部署（推荐 ⭐）

使用自动初始化脚本，一键完成所有设置：

```bash
# 1. 运行初始化脚本（自动设置权限和配置）
./init-deployment.sh

# 2. 编辑配置文件
vim .env          # 配置 API 密钥
vim config.yaml   # 配置交易参数

# 3. 构建并启动容器
docker-compose up -d --build

# 4. 查看日志
docker-compose logs -f
```

初始化脚本会自动：
- ✅ 创建必要的目录结构
- ✅ 设置正确的文件权限（避免权限错误）
- ✅ 复制配置文件模板
- ✅ 检查 Docker 环境
- ✅ 提供详细的下一步指引

### 方法二：手动部署

### 1. Clone the Repository

```bash
git clone <repository-url>
cd quant-flow
```

### 2. 设置日志目录权限（重要！）

**这一步非常重要，避免权限错误：**

```bash
# 创建日志目录
mkdir -p logs/decisions logs/trades

# 设置权限（允许容器写入）
chmod -R 777 logs/
```

**为什么需要这一步？**

Docker 容器内使用 `quantflow` 用户（UID 1000）运行，需要写入权限才能创建日志文件。不设置权限会导致以下错误：

```
PermissionError: [Errno 13] Permission denied: '/app/logs/trading_20251115.log'
```

### 3. Configure Environment Variables

Create a `.env` file from the example:

```bash
cp .env.example .env
```

Edit `.env` and configure your credentials:

```bash
# OpenAI/DeepSeek API Configuration
OPENAI_API_KEY=your_api_key_here
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# Hyperliquid Configuration
HYPERLIQUID_PRIVATE_KEY=0xYourPrivateKeyHere
HYPERLIQUID_ACCOUNT_ADDRESS=
HYPERLIQUID_TESTNET=true

# Application Configuration
LOG_LEVEL=INFO
```

**⚠️ SECURITY WARNING:**
- Never commit `.env` file to version control
- Keep your private keys secure
- Use testnet mode for initial testing

### 3. Configure Trading Parameters

Create `config.yaml` from the example:

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` to customize your trading strategy:

```yaml
trading:
  symbols: [BTC, ETH]
  max_trade_amount: 100
  initial_balance: 10000.0
  take_profit_ratio: 0.05
  stop_loss_ratio: 0.02
  max_positions: 2
  max_leverage: 10

scheduler:
  interval_minutes: 3
  run_immediately: true
```

### 4. Build and Run

Build the Docker image:

```bash
docker-compose build
```

Start the bot:

```bash
docker-compose up -d
```

### 5. Monitor Logs

View real-time logs:

```bash
docker-compose logs -f quant-flow
```

View recent logs:

```bash
docker-compose logs --tail=100 quant-flow
```

## Management Commands

### Start the Bot

```bash
docker-compose up -d
```

### Stop the Bot

```bash
docker-compose down
```

### Restart the Bot

```bash
docker-compose restart
```

### Check Status

```bash
docker-compose ps
```

### View Resource Usage

```bash
docker stats quant-flow-bot
```

## Directory Structure

```
quant-flow/
├── Dockerfile              # Docker image definition
├── docker-compose.yml      # Docker Compose orchestration
├── .dockerignore          # Files to exclude from build
├── .env                   # Environment variables (create from .env.example)
├── config.yaml            # Trading configuration (create from example)
├── prompts/               # AI prompt templates (read-only in container)
└── logs/                  # Trading logs (persistent volume)
    ├── decisions/         # Decision logs
    └── trades/           # Trade execution logs
```

## Persistent Data

The following data is persisted outside the container:

- **`./logs`**: Trading decision and execution logs
- **`./config.yaml`**: Trading configuration (mounted read-only)
- **`./prompts`**: AI prompt templates (mounted read-only)

Logs are stored in `./logs` on the host machine and persist across container restarts.

## Configuration Options

### Environment Variables

All environment variables can be set in `.env` or directly in `docker-compose.yml`:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `OPENAI_API_KEY` | API key for OpenAI/DeepSeek | - | ✅ |
| `OPENAI_API_BASE` | API base URL | `https://api.deepseek.com/v1` | ❌ |
| `OPENAI_MODEL` | Model name | `deepseek-chat` | ❌ |
| `HYPERLIQUID_PRIVATE_KEY` | Wallet private key (0x-prefixed) | - | ✅ |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | Account address for API wallet | - | ❌ |
| `HYPERLIQUID_TESTNET` | Use testnet | `true` | ❌ |
| `LOG_LEVEL` | Logging level | `INFO` | ❌ |
| `TRADE_AMOUNT` | Default trade amount USD | From config.yaml | ❌ |
| `TAKE_PROFIT_RATIO` | Take profit percentage | From config.yaml | ❌ |
| `STOP_LOSS_RATIO` | Stop loss percentage | From config.yaml | ❌ |
| `DEFAULT_LEVERAGE` | Default leverage | From config.yaml | ❌ |

### Resource Limits

The default resource limits in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 1G
    reservations:
      cpus: '0.5'
      memory: 256M
```

Adjust these based on your server capacity.

## Production Deployment

### 1. Switch to Mainnet

Edit `.env`:

```bash
HYPERLIQUID_TESTNET=false
```

**⚠️ WARNING:** Only use mainnet with funds you can afford to lose. Test thoroughly on testnet first.

### 2. Configure Auto-Restart

The bot is configured with `restart: unless-stopped`, which means:
- Automatically restarts on failure
- Restarts after server reboot
- Stops only when explicitly stopped via `docker-compose down`

### 3. Set Up Monitoring

Monitor the bot's health:

```bash
# Check if container is healthy
docker inspect --format='{{.State.Health.Status}}' quant-flow-bot

# View health check logs
docker inspect --format='{{json .State.Health}}' quant-flow-bot | jq
```

### 4. Configure Notifications

Enable notifications in `config.yaml`:

```yaml
notifications:
  enabled: true
  channels:
    - type: dingtalk
      webhook_url: "your_dingtalk_webhook"
    - type: feishu
      webhook_url: "your_feishu_webhook"
      secret: "your_secret"
    - type: email
      smtp_server: "smtp.gmail.com"
      smtp_port: 587
      username: "your_email@gmail.com"
      password: "your_app_password"
      from_addr: "your_email@gmail.com"
      to_addrs: ["recipient@example.com"]
```

### 5. Log Rotation

Logs are automatically rotated with these settings:

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

This keeps a maximum of 30MB of Docker logs (3 files × 10MB).

## Troubleshooting

### ❌ 权限错误（最常见问题）

**问题：** 容器启动后不断重启，日志显示：

```
PermissionError: [Errno 13] Permission denied: '/app/logs/trading_20251115.log'
```

**原因：** Docker 容器内的 `quantflow` 用户（UID 1000）无法写入宿主机的 `logs/` 目录。

**解决方案（按推荐顺序）：**

#### 方案 1：使用初始化脚本（最简单）

```bash
# 停止容器
docker-compose down

# 运行初始化脚本
./init-deployment.sh

# 重新启动
docker-compose up -d
```

#### 方案 2：手动修复权限

```bash
# 停止容器
docker-compose down

# 修复权限
chmod -R 777 logs/

# 重新启动
docker-compose up -d
```

#### 方案 3：删除并重建（彻底清理）

```bash
# 停止并删除容器
docker-compose down

# 删除旧的 logs 目录
rm -rf logs/

# 重新创建并设置权限
mkdir -p logs/decisions logs/trades
chmod -R 777 logs/

# 重新启动
docker-compose up -d
```

#### 验证修复成功

```bash
# 查看容器状态（应该是 Up 状态）
docker-compose ps

# 查看日志（不应该有权限错误）
docker-compose logs --tail 50

# 检查日志文件是否生成
ls -la logs/
# 应该看到 trading_YYYYMMDD.log 文件
```

### Container Won't Start

Check logs for errors:

```bash
docker-compose logs quant-flow
```

Common issues:
- Missing `.env` file
- Invalid API keys
- Missing `config.yaml`
- **权限问题**（见上面的详细说明）
- Port conflicts (unlikely for this bot)

### Container Exits Immediately

1. Check if configuration files exist:
   ```bash
   ls -la config.yaml .env
   ```

2. Validate environment variables:
   ```bash
   docker-compose config
   ```

3. Test the bot locally first:
   ```bash
   python main.py
   ```

### API Connection Issues

1. Verify API credentials in `.env`
2. Check internet connectivity from container:
   ```bash
   docker exec quant-flow-bot ping -c 3 api.deepseek.com
   ```

3. Check firewall rules for outbound HTTPS (port 443)

### High Resource Usage

Monitor resource usage:

```bash
docker stats quant-flow-bot
```

If CPU/memory is too high:
- Increase `scheduler.interval_minutes` in `config.yaml`
- Reduce number of symbols in trading configuration
- Lower `agent.max_iterations` in config

### Logs Not Persisting

Ensure the logs directory is properly mounted:

```bash
docker inspect quant-flow-bot | jq '.[0].Mounts'
```

The output should show `/app/logs` mounted to `./logs`.

## Updating the Bot

### Update Code

```bash
# Pull latest changes
git pull

# Rebuild the image
docker-compose build

# Restart with new image
docker-compose up -d
```

### Update Configuration

```bash
# Edit config.yaml or .env
nano config.yaml

# Restart to apply changes
docker-compose restart
```

Configuration changes require a restart to take effect.

## Backup and Recovery

### Backup

Backup these critical files:

```bash
# Create backup directory
mkdir -p backups/$(date +%Y%m%d)

# Backup configuration
cp .env config.yaml backups/$(date +%Y%m%d)/

# Backup logs
cp -r logs/ backups/$(date +%Y%m%d)/
```

### Recovery

```bash
# Restore configuration
cp backups/20240315/.env .
cp backups/20240315/config.yaml .

# Restart bot
docker-compose restart
```

## Security Best Practices

1. **Never commit secrets**: Ensure `.env` is in `.gitignore`
2. **Use environment variables**: Don't hardcode credentials
3. **Run as non-root**: The Dockerfile uses a dedicated `quantflow` user
4. **Read-only mounts**: Configuration files are mounted read-only
5. **Network isolation**: The bot uses a dedicated Docker network
6. **Regular updates**: Keep Docker and the bot code updated
7. **Monitor logs**: Regularly check for suspicious activity
8. **Testnet first**: Always test changes on testnet before mainnet

## Performance Tuning

### Optimize Scheduling

For lower latency:

```yaml
scheduler:
  interval_minutes: 1  # Check every minute
```

For lower resource usage:

```yaml
scheduler:
  interval_minutes: 15  # Check every 15 minutes
```

### Optimize Memory

Reduce memory footprint:

```yaml
agent:
  memory:
    max_token_limit: 1000  # Reduce token limit
    max_messages: 5        # Keep fewer messages
```

### Optimize API Calls

Reduce API costs:

```yaml
agent:
  temperature: 0.0       # Deterministic responses
  max_iterations: 3      # Fewer iterations

data:
  candles_limit: 50      # Fetch fewer candles
```

## Support

For issues and questions:

1. Check the logs: `docker-compose logs -f`
2. Review this guide and main README.md
3. Open an issue on GitHub
4. Check Docker and Docker Compose versions

## Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Main README](README.md)
- [Quick Start Guide](QUICKSTART.md)
- [Configuration Guide](FLEXIBLE_CONFIG_GUIDE.md)
