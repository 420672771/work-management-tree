#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/worktree.pid"
if [[ ! -f "$PID_FILE" ]]; then echo "未发现 PID 文件"; exit 0; fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; echo "已停止 PID $PID"; else echo "进程不存在"; fi
rm -f "$PID_FILE"
