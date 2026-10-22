#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for name in worktree-daily-morning worktree-daily-evening worktree-approval-reminder; do
  systemctl --user link "$root_dir/deploy/$name.service"
  systemctl --user link "$root_dir/deploy/$name.timer"
  systemctl --user enable --now "$name.timer"
done
systemctl --user list-timers 'worktree-*' --no-pager
