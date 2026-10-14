#!/usr/bin/env python3
import importlib.util, json, os, socket
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
required=('WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET','WECOM_TOKEN','WECOM_AES_KEY')
checks={
    'project_files': all((ROOT/p).exists() for p in ('app.py','static/index.html','docs/PRD.md')),
    'crypto_dependency': importlib.util.find_spec('Crypto') is not None,
    'wecom_configured': all(os.getenv(k) for k in required),
    'callback_key_length': len(os.getenv('WECOM_AES_KEY','')) in (0,43),
}
# The running service is checked separately by healthcheck.sh.
checks['local_service_check_command']=str(ROOT/'scripts/healthcheck.sh')
print(json.dumps({'ready_for_real_wecom':checks['wecom_configured'] and checks['crypto_dependency'] and checks['project_files'],'checks':checks},ensure_ascii=False,indent=2))
