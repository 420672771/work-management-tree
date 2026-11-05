#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${WMT_PUBLIC_USER:?请设置 WMT_PUBLIC_USER}"
: "${WMT_PUBLIC_PASSWORD:?请设置 WMT_PUBLIC_PASSWORD}"
echo "公共访问模式已启用：应用将要求 HTTP Basic Auth。"
exec "$ROOT_DIR/scripts/start-local.sh"
