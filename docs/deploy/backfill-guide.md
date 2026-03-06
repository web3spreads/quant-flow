# 历史数据回填指南

## 概述

`backfill_qlib_data.py` 是独立的历史 K 线数据回填工具，从 Hyperliquid API 批量拉取数据并以 Parquet 格式存储到本地。回填的数据供 QLib 引擎训练和实盘交易使用。

**核心特性：**

- 支持按天数或日期范围回填
- 自动分批请求（单次上限 500 条），内置限流和重试
- 增量合并：新数据与本地已有数据按 `timestamp` 去重合并，不会产生重复
- 支持预览模式（`--dry-run`），不写入文件

## 数据存储

| 项目 | 说明 |
|------|------|
| 存储路径 | `data/qlib/` |
| 文件格式 | Parquet |
| 命名规则 | `{symbol}_{freq}.parquet`（如 `BTC_1h.parquet`） |
| 列 | `timestamp`(datetime), `open`, `high`, `low`, `close`, `volume`(float) |

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--symbols` | `BTC ETH SOL` | 交易对列表 |
| `--freq` | `1h` | K 线频率，可选：`1m` `5m` `15m` `1h` `4h` `1d` |
| `--days` | `90` | 回填最近 N 天数据 |
| `--start-date` | 无 | 开始日期 `YYYY-MM-DD`，优先于 `--days` |
| `--end-date` | 当前时间 | 结束日期 `YYYY-MM-DD` |
| `--data-dir` | `data/qlib` | 数据存储目录 |
| `--request-interval` | `2.0` | API 请求间隔（秒），防止限流 |
| `--testnet` | 否 | 使用测试网 |
| `--dry-run` | 否 | 只预览，不写入文件 |

## 本地运行

```bash
# 回填最近 90 天数据（默认参数）
uv run python backfill_qlib_data.py

# 回填指定天数
uv run python backfill_qlib_data.py --days 180

# 回填指定日期范围
uv run python backfill_qlib_data.py --start-date 2025-09-01 --end-date 2026-03-06

# 指定交易对和频率
uv run python backfill_qlib_data.py --symbols BTC ETH SOL DOGE --freq 4h

# 使用测试网
uv run python backfill_qlib_data.py --testnet

# 只预览不写入
uv run python backfill_qlib_data.py --dry-run
```

## Docker 运行

回填服务在 `docker-compose.yml` 中定义为 `backfill` 服务，使用 `RUN_MODE=backfill` 启动。运行完自动退出，不会常驻后台。

### 基本用法

```bash
# 使用默认参数回填（最近 90 天，BTC ETH SOL，1h 频率）
docker compose run --rm backfill

# 指定回填参数
docker compose run --rm -e BACKFILL_ARGS="--days 180" backfill

# 回填指定交易对
docker compose run --rm -e BACKFILL_ARGS="--symbols BTC ETH SOL DOGE --freq 4h" backfill

# 回填指定日期范围
docker compose run --rm -e BACKFILL_ARGS="--start-date 2025-06-01 --end-date 2026-03-01" backfill

# 使用测试网
docker compose run --rm -e BACKFILL_ARGS="--testnet" backfill

# 预览模式
docker compose run --rm -e BACKFILL_ARGS="--dry-run --days 30" backfill
```

### 通过 `.env` 文件配置默认参数

在 `.env` 文件中设置 `BACKFILL_ARGS`，后续运行时无需每次指定：

```bash
# .env
BACKFILL_ARGS=--days 180 --symbols BTC ETH SOL --freq 1h
```

```bash
# 直接运行，使用 .env 中的默认参数
docker compose run --rm backfill

# 也可以在运行时覆盖
docker compose run --rm -e BACKFILL_ARGS="--days 30" backfill
```

### 数据卷说明

回填服务挂载了 `./data:/app/data`，回填结果直接写入宿主机的 `data/qlib/` 目录。

主交易服务如果需要使用回填数据，也需要挂载相同的 `data` 目录。在 `docker-compose.yml` 的 `quant-flow` 服务中添加：

```yaml
volumes:
  - ./data:/app/data
```

## 典型工作流

### 1. 首次部署：回填历史数据后启动交易

```bash
# 第一步：回填 180 天历史数据
docker compose run --rm -e BACKFILL_ARGS="--days 180" backfill

# 第二步：确认数据已写入
ls -lh data/qlib/

# 第三步：启动主交易
docker compose up -d quant-flow
```

### 2. 定期补充数据

```bash
# 每周补充最近 7 天数据（增量合并，自动去重）
docker compose run --rm -e BACKFILL_ARGS="--days 7" backfill
```

### 3. 新增交易对数据

```bash
# 为新增的交易对回填历史数据
docker compose run --rm -e BACKFILL_ARGS="--symbols DOGE WIF --days 90" backfill
```

## 注意事项

- 本脚本**只负责回填数据**，不触发模型训练。训练仍按 QLib 引擎配置的 `retrain_interval_hours` 自动执行
- API 请求间隔默认 2 秒，回填大量数据时耗时较长（如 180 天 × 3 个交易对 ≈ 10 分钟）
- 增量合并安全：重复运行不会产生重复数据
- 回填完成后 QLib 训练时会自动使用全量本地数据（`use_all_local=True`）
