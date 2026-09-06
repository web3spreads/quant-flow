#!/usr/bin/env bash
# book-health.mjs 的 systemd 包装：解析通知 URL 后执行。
# 通知 URL 优先取环境变量 QUANTFLOW_NOTIFY_URLS（/etc/quantflow/health.env）；缺省从共享 .env
# （QUANTFLOW_SHARED_ENV，默认 dsh 的 .env）只抽 POLYSNIPE_NOTIFY_URLS 这一个变量，不把整个文件暴露给进程。
# 逻辑放在脚本里而不是 unit 的 ExecStart：systemd 会先对 ExecStart 做自己的 $ 展开，内嵌 shell 表达式会被吃掉。
set -euo pipefail
export PATH="/home/ubuntu/.nvm/versions/node/v22.20.0/bin:${PATH}"
SHARED_ENV="${QUANTFLOW_SHARED_ENV:-/home/ubuntu/code/runtime/.dsh/.env}"
if [[ -z "${QUANTFLOW_NOTIFY_URLS:-}" && -r "$SHARED_ENV" ]]; then
  QUANTFLOW_NOTIFY_URLS="$(grep -m1 '^POLYSNIPE_NOTIFY_URLS=' "$SHARED_ENV" | cut -d= -f2- | tr -d "\"'" || true)"
  export QUANTFLOW_NOTIFY_URLS
fi
exec node "$(dirname "$(readlink -f "$0")")/book-health.mjs" "$@"
