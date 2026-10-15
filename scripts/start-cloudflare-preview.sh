#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${WMT_PUBLIC_USER:?请先设置 WMT_PUBLIC_USER}"
: "${WMT_PUBLIC_PASSWORD:?请先设置 WMT_PUBLIC_PASSWORD}"
if ! curl -fsS -u "$WMT_PUBLIC_USER:$WMT_PUBLIC_PASSWORD" http://127.0.0.1:8080/api/dashboard >/dev/null; then
  echo '本地服务未运行，请先执行 ./scripts/start-public.sh' >&2
  exit 1
fi
if [[ ! -x "$ROOT_DIR/bin/cloudflared" ]]; then
  echo '缺少 bin/cloudflared，请安装 Cloudflare Tunnel 客户端' >&2
  exit 1
fi
exec "$ROOT_DIR/bin/cloudflared" tunnel --url http://127.0.0.1:8080
