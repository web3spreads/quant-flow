# 盘口微观结构研究：数据管线

阶段 B 研究的代码目录。**预注册的假设、门槛与判定规则不放在仓库里**（在工作区文档中，
研究开始前冻结），这里只有数据管线与假设脚本的实现。

## 一条命令重建

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
RAW=/path/to/book PARQUET=/path/to/parquet OUT=./out ./run.sh
```

- `RAW`：录制器输出目录（`<COIN>/<YYYY-MM-DD>/{l2book,l2full,trades,bbo,ctx}.jsonl.gz` + `manifest.json`）
- `PARQUET`：列式中间层，`<COIN>/<YYYY-MM-DD>/<channel>.parquet`，按交易所时间 `t` 排序、去重
- `OUT`：质量表与后续结果；随机种子固定为 `QF_SEED`（默认 20261003）

`run.sh` 幂等：已转换且原始文件未变的日子直接跳过，`--force` 重做。

## 质量表（纳入门槛）

`pipeline/quality.py` 对每个「标的 × 日」给出 l2book 覆盖率（有数据的秒数 / 86400，优先取清单里
录制器实时统计，缺清单统计时按 parquet 重算）、缺口数、最大间隔、收包延迟与 RTT 分位数，并按
`--min-coverage`（默认 0.95）标出是否纳入。**未达标的日子剔除并在报告列出**，不做插补。

## 开发切片

研究正式开始前用最早的几天做管线开发与假设脚本的联调；这些天标为开发切片，最终检验时
**整体剔除**，假设与门槛不因开发切片上看到的任何结果而修改。

## 目录

- `pipeline/ingest.py`：jsonl.gz → parquet（多成员 gzip、排序、去重、类型化、`_ingest.json` 计数）
- `pipeline/quality.py`：质量表（CSV + Markdown）
- `pipeline/dataset.py`：跨日拼接读取（`load(coin, channel, dates)` 返回 polars LazyFrame）
- `tests/`：合成数据上的管线测试（`.venv/bin/pytest research/tests`）
