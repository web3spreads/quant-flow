#!/usr/bin/env bash
# 盘口数据异地备份：把 <BOOK_DIR>/<COIN>/<日>（早于今天且已有 manifest.json 的目录）rsync 到
# BOOK_BACKUP_DEST，成功后在本地目录写 .backed-up 标记；本地只保留最近 BOOK_LOCAL_KEEP_DAYS 天
# （且必须带 .backed-up 标记才删）。清单里有 sha256，远端可用 scripts/book-verify.mjs 复核。
#
# 环境变量（deploy/book-backup.env.example）：
#   BOOK_DIR               本地数据目录（默认 /home/ubuntu/quantflow/data/book）
#   BOOK_BACKUP_DEST       rsync 目标，如 user@host:/srv/quantflow-book 或 /mnt/backup/book
#   BOOK_BACKUP_SSH_OPTS   传给 rsync -e ssh 的参数（默认 -o BatchMode=yes -o StrictHostKeyChecking=yes）
#   BOOK_LOCAL_KEEP_DAYS   本地保留天数（默认 45）
#   BOOK_REMOTE_KEEP_DAYS  远端保留天数（默认 90；0=不清理远端）
#
# 用法：
#   scripts/book-backup.sh            # 备份 + 本地清理 + 远端清理
#   scripts/book-backup.sh --check    # 只列计划与远端可达性，不改任何东西
set -euo pipefail

BOOK_DIR="${BOOK_DIR:-/home/ubuntu/quantflow/data/book}"
DEST="${BOOK_BACKUP_DEST:-}"
SSH_OPTS="${BOOK_BACKUP_SSH_OPTS:--o BatchMode=yes -o StrictHostKeyChecking=yes}"
LOCAL_KEEP="${BOOK_LOCAL_KEEP_DAYS:-45}"
REMOTE_KEEP="${BOOK_REMOTE_KEEP_DAYS:-90}"
CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "❌ $*"; exit 2; }

[[ -n "$DEST" ]] || die "未设置 BOOK_BACKUP_DEST"
[[ -d "$BOOK_DIR" ]] || die "本地目录不存在: $BOOK_DIR"
command -v rsync >/dev/null || die "缺少 rsync"

today="$(date -u +%F)"
remote_host=""; remote_path="$DEST"
if [[ "$DEST" == *:* ]]; then remote_host="${DEST%%:*}"; remote_path="${DEST#*:}"; fi

# 远端可达性 / 目录就位
if [[ -n "$remote_host" ]]; then
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$remote_host" "mkdir -p '$remote_path'" || die "远端不可达或无法创建目录: $DEST"
else
  mkdir -p "$remote_path"
fi

planned=0; done_count=0; failed=0
while IFS= read -r day_dir; do
  coin="$(basename "$(dirname "$day_dir")")"; day="$(basename "$day_dir")"
  [[ "$day" < "$today" ]] || continue
  [[ -f "$day_dir/manifest.json" ]] || { log "跳过 $coin/$day：尚无 manifest.json"; continue; }
  planned=$((planned + 1))
  if [[ $CHECK -eq 1 ]]; then
    if [[ -f "$day_dir/.backed-up" ]]; then log "已备份 $coin/$day"; else log "待备份 $coin/$day ($(du -sh "$day_dir" | cut -f1))"; fi
    continue
  fi
  # 幂等：rsync 按 checksum 比对，已备份目录只会传清单差异
  if [[ -n "$remote_host" ]]; then
    # shellcheck disable=SC2086
    if rsync -az --partial --checksum -e "ssh $SSH_OPTS" --exclude '.backed-up' "$day_dir/" "$DEST/$coin/$day/"; then
      date -u +%FT%TZ > "$day_dir/.backed-up"; done_count=$((done_count + 1))
    else
      failed=$((failed + 1)); log "❌ 备份失败 $coin/$day"
    fi
  else
    if rsync -a --partial --checksum --exclude '.backed-up' "$day_dir/" "$remote_path/$coin/$day/"; then
      date -u +%FT%TZ > "$day_dir/.backed-up"; done_count=$((done_count + 1))
    else
      failed=$((failed + 1)); log "❌ 备份失败 $coin/$day"
    fi
  fi
done < <(find "$BOOK_DIR" -mindepth 2 -maxdepth 2 -type d -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort)

log "计划 $planned 个日目录，本次完成 $done_count，失败 $failed$([[ $CHECK -eq 1 ]] && echo '（--check，未改动）')"
[[ $CHECK -eq 1 ]] && exit 0

# 本地清理：早于 LOCAL_KEEP 天且带 .backed-up 标记
cutoff_local="$(date -u -d "-${LOCAL_KEEP} days" +%F)"
while IFS= read -r day_dir; do
  day="$(basename "$day_dir")"
  [[ "$day" < "$cutoff_local" && -f "$day_dir/.backed-up" ]] || continue
  log "本地清理 $(basename "$(dirname "$day_dir")")/$day（已备份，早于 ${LOCAL_KEEP} 天）"
  rm -rf "$day_dir"
done < <(find "$BOOK_DIR" -mindepth 2 -maxdepth 2 -type d -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort)

# 远端清理（可选）
if [[ "$REMOTE_KEEP" -gt 0 ]]; then
  cutoff_remote="$(date -u -d "-${REMOTE_KEEP} days" +%F)"
  prune="cd '$remote_path' && find . -mindepth 2 -maxdepth 2 -type d -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}\$' | while read -r d; do [[ \"\$(basename \"\$d\")\" < '$cutoff_remote' ]] && rm -rf \"\$d\" && echo \"远端清理 \$d\"; done; true"
  if [[ -n "$remote_host" ]]; then
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$remote_host" "bash -c \"$prune\"" || log "⚠️ 远端清理失败（不影响备份）"
  else
    bash -c "$prune" || true
  fi
fi

[[ $failed -eq 0 ]] || exit 1
