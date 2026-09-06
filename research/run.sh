#!/usr/bin/env bash
# 一条命令从原始录制文件重建全部中间结果：ingest → quality（后续假设脚本按序追加）。
set -euo pipefail
cd "$(dirname "$0")"
RAW="${RAW:?RAW=录制器输出目录}"
PARQUET="${PARQUET:?PARQUET=列式中间层目录}"
OUT="${OUT:-./out}"
export QF_SEED="${QF_SEED:-20261003}"
PY="${PY:-$(command -v python3)}"
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
mkdir -p "$OUT"
"$PY" -m pipeline.ingest --raw "$RAW" --out "$PARQUET" "$@"
"$PY" -m pipeline.quality --raw "$RAW" --parquet "$PARQUET" --out "$OUT/quality.csv" --markdown "$OUT/quality.md"
echo "完成：$OUT/quality.md"
