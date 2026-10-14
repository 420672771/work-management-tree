#!/usr/bin/env bash
set -euo pipefail
PORT="${PORT:-8080}"
for attempt in 1 2 3 4 5; do
  if python3 - <<PY
import json, sys, urllib.request
try:
    url='http://127.0.0.1:${PORT}/api/dashboard'
    print(json.dumps(json.load(urllib.request.urlopen(url, timeout=2)), ensure_ascii=False))
except Exception:
    sys.exit(1)
PY
  then exit 0; fi
  sleep 1
done
echo "健康检查失败：服务未在 ${PORT} 端口就绪" >&2
exit 1
