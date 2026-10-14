#!/usr/bin/env bash
set -euo pipefail
PORT="${PORT:-8080}"
for attempt in 1 2 3 4 5; do
  if python3 - <<PY
import base64, json, os, sys, urllib.request
try:
    url='http://127.0.0.1:${PORT}/api/dashboard'
    req=urllib.request.Request(url)
    user, password=os.getenv('WMT_PUBLIC_USER'), os.getenv('WMT_PUBLIC_PASSWORD')
    if user and password:
        req.add_header('Authorization','Basic '+base64.b64encode((user+':'+password).encode()).decode())
    print(json.dumps(json.load(urllib.request.urlopen(req, timeout=2)), ensure_ascii=False))
except Exception:
    sys.exit(1)
PY
  then exit 0; fi
  sleep 1
done
echo "健康检查失败：服务未在 ${PORT} 端口就绪" >&2
exit 1
