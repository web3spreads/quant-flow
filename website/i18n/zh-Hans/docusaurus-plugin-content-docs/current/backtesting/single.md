---
sidebar_position: 1
title: 单 Agent 回测
description: 永续合约策略回测指南
---

# 单 Agent 回测

对永续合约 Agent 策略进行历史数据回测。

## 基本用法

```bash
uv run python backtest.py \
  --symbol BTC \
  --strategy single \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

## 常用参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--symbol` | 交易对符号 | `BTC`, `ETH` |
| `--strategy` | 策略类型 | `single`（默认） |
| `--start-date` | 回测开始日期 | `2024-01-01` |
| `--end-date` | 回测结束日期 | `2024-12-01` |
| `--resume-from` | 从检查点恢复 | 见下文 |

## 中断恢复

回测过程中断后可从 `live_report.json` 恢复：

```bash
uv run python backtest.py \
  --resume-from backtest_results/backtest_BTC_xxx/live_report.json
```

## 回测报告

结果保存在 `backtest_results/` 目录，包含：
- `live_report.json` — 实时更新的回测状态
- 最终绩效统计（总收益、最大回撤、胜率等）

更多详情参考 [BACKTEST_README.md](https://github.com/web3spreads/quant-flow/blob/main/BACKTEST_README.md)。
