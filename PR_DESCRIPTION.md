# Add Docker Compose support for easy server deployment

## 🐳 Summary

This PR adds complete Docker containerization support for the quant-flow trading bot, making it easy to deploy on any server with a single command.

## ✨ Features Added

### Core Docker Files
- **Dockerfile** - Multi-stage build with Python 3.13-slim for optimized image size
  - Non-root user (quantflow) for enhanced security
  - Health checks for container monitoring
  - Optimized layer caching and minimal dependencies
- **docker-compose.yml** - Complete orchestration configuration
  - Environment variable management
  - Volume mounts for config, prompts, and persistent logs
  - Resource limits (CPU: 2 cores, Memory: 1GB)
  - Auto-restart policy and health checks
  - Log rotation configuration
- **.dockerignore** - Optimized build context to exclude unnecessary files
- **DOCKER_DEPLOYMENT.md** - Comprehensive deployment and troubleshooting guide

### Infrastructure Support
- **Docker daemon enabled** in .idx/dev.nix for development environment
- **Log directories** with proper .gitkeep files

### Documentation Updates
- **README.md** - Added Docker deployment section (marked as recommended method)

## 🚀 Quick Start

```bash
# 1. Configure environment
cp .env.example .env
cp config.yaml.example config.yaml
# Edit .env and config.yaml with your credentials

# 2. Start the bot
docker-compose up -d

# 3. View logs
docker-compose logs -f quant-flow
```

## 📋 Files Changed

| File | Changes | Lines |
|------|---------|-------|
| `Dockerfile` | ➕ Added | 51 |
| `docker-compose.yml` | ➕ Added | 83 |
| `.dockerignore` | ➕ Added | 76 |
| `DOCKER_DEPLOYMENT.md` | ➕ Added | 454 |
| `README.md` | ✏️ Modified | +21/-1 |
| `.idx/dev.nix` | ✏️ Modified | +4 |
| `logs/.gitkeep` | ✏️ Permission | - |
| `logs/decisions/.gitkeep` | ✏️ Permission | - |
| `logs/trades/.gitkeep` | ✏️ Permission | - |

**Total**: 9 files changed, 688 insertions(+), 1 deletion(-)

## 🎯 Key Benefits

1. **One-command deployment** - `docker-compose up -d` starts everything
2. **Security** - Non-root user, read-only config mounts, no-new-privileges
3. **Persistence** - Logs directory persists across container restarts
4. **Auto-restart** - Automatically restarts on failure or server reboot
5. **Resource management** - Configurable CPU/memory limits
6. **Health monitoring** - Built-in health checks for container status
7. **Log rotation** - Automatic log management (3×10MB files)
8. **Production-ready** - Follows Docker best practices

## 🔧 Technical Details

### Multi-stage Build
- **Builder stage**: Compiles dependencies with gcc/g++
- **Final stage**: Minimal runtime image (Python 3.13-slim)
- **Result**: Optimized image size with faster builds

### Security Features
- Runs as non-root user (`quantflow:1000`)
- Read-only mounts for configuration and prompts
- Security option: `no-new-privileges:true`
- No ports exposed (headless bot)

### Volume Management
```yaml
volumes:
  - ./config.yaml:/app/config.yaml:ro     # Read-only config
  - ./prompts:/app/prompts:ro             # Read-only prompts
  - ./logs:/app/logs                      # Persistent logs
```

### Environment Variables
All sensitive data managed via `.env` file:
- `OPENAI_API_KEY` - API key for DeepSeek/OpenAI
- `HYPERLIQUID_PRIVATE_KEY` - Wallet private key
- `HYPERLIQUID_TESTNET` - Testnet/mainnet toggle
- Plus additional configuration options

## 📖 Documentation

The new `DOCKER_DEPLOYMENT.md` includes:
- Complete deployment guide
- Production best practices
- Troubleshooting section
- Performance tuning tips
- Backup and recovery procedures
- Security best practices

## ✅ Testing Checklist

- [x] Dockerfile builds successfully with multi-stage optimization
- [x] Docker Compose orchestration works correctly
- [x] Health checks function properly
- [x] Volumes mount correctly (config, prompts, logs)
- [x] Environment variables load from .env
- [x] Non-root user has correct permissions
- [x] Log rotation configured
- [x] Auto-restart policy works
- [x] Documentation is comprehensive

## 🔍 Commits

1. **f510e24** - Add Docker containerization support for easy deployment
2. **175d094** - Fix docker build (README.md needed for package metadata)

## 📚 Related Documentation

- See [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) for complete deployment guide
- See [README.md](README.md) for quick start
- See [QUICKSTART.md](QUICKSTART.md) for traditional installation

## 🎉 Impact

This PR makes it significantly easier for users to deploy the trading bot on any server with Docker installed. No more Python version conflicts, dependency issues, or complex setup procedures - just configure and run!
