#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
marker="$root_dir/data/.auto-reload-enabled"

if [[ "${1:-}" == "--help" ]]; then
  echo '用法：bash scripts/configure-auto-reload.sh [--disable]'
  echo '在服务器上以运行 worktree.service 的用户执行；不改变服务原有配置。'
  exit 0
fi
if [[ -n "${1:-}" && "$1" != "--disable" ]]; then
  echo '不支持的参数；请使用 --help 查看用法。' >&2
  exit 2
fi

# Check service access before changing anything. This script must be invoked
# from an authorized server terminal, not used to bypass executor isolation.
systemctl --user show worktree.service --property=LoadState --value | python3 -c '
import sys
if sys.stdin.read().strip() != "loaded":
    raise SystemExit("未能连接当前用户的 worktree.service，请在服务器终端执行。")
'
working_dir="$(systemctl --user show worktree.service --property=WorkingDirectory --value)"
if [[ "$working_dir" != "$root_dir" ]]; then
  echo '服务工作目录与当前项目不一致，设置未修改。' >&2
  exit 1
fi

was_enabled=false
if [[ -f "$marker" ]]; then was_enabled=true; fi
if [[ "${1:-}" == "--disable" ]]; then
  if ! $was_enabled; then echo '自动重载当前未启用。'; exit 0; fi
  rm "$marker"
else
  python3 "$root_dir/scripts/run-dev-server.py" --check
  mkdir -p "$root_dir/data"
  (umask 077; printf 'enabled\n' > "$marker")
fi

if ! systemctl --user restart worktree.service; then
  if $was_enabled; then
    (umask 077; printf 'enabled\n' > "$marker")
  else
    rm -f "$marker"
  fi
  echo '服务重启失败，自动重载开关已恢复原状态。' >&2
  exit 1
fi
systemctl --user is-active --quiet worktree.service
if [[ "${1:-}" == "--disable" ]]; then
  echo '自动重载已关闭，服务恢复普通启动方式。'
else
  echo '自动重载已启用，现有端口、数据库和认证配置均由原服务继续提供。'
  echo '后端源码稳定 2 秒且检查通过后自动重载；前端修改刷新页面即可。'
  echo '日志：journalctl --user -u worktree.service -f'
fi
