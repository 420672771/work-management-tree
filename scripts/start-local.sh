#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/data"

if [[ -f "$ROOT_DIR/worktree.pid" ]] && kill -0 "$(cat "$ROOT_DIR/worktree.pid")" 2>/dev/null; then
  echo "工作管理树已运行，PID $(cat "$ROOT_DIR/worktree.pid")"
  exit 0
fi

cd "$ROOT_DIR"
nohup env PORT="${PORT:-8080}" WMT_DB="${WMT_DB:-$ROOT_DIR/data/worktree.db}" python3 app.py >"$ROOT_DIR/logs/worktree.log" 2>&1 &
echo $! > "$ROOT_DIR/worktree.pid"
echo "已启动：http://127.0.0.1:${PORT:-8080}（PID $(cat "$ROOT_DIR/worktree.pid")）"
