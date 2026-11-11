"""Run the real pending-draft UI against a disposable account-mode database."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import accounts


def main():
    environment = {'WMT_AUTH_MODE': 'account', 'WMT_MONTHLY_DEMO': '0'}
    environment.update({key: '' for key in os.environ if key.startswith(('AI_', 'FEISHU_', 'WECOM_'))})
    with tempfile.TemporaryDirectory(prefix='wmt-pending-drafts-') as folder, patch.dict(os.environ, environment), patch.object(app, 'DB_PATH', Path(folder) / 'fixture.db'):
        app.initialize()
        c = app.db()
        users = {role: c.execute('SELECT id FROM user WHERE role=? LIMIT 1', (role,)).fetchone()[0]
                 for role in ('manager', 'pm', 'member')}
        users['other'] = c.execute("SELECT id FROM user WHERE role='member' AND id!=? LIMIT 1", (users['member'],)).fetchone()[0]
        for role, uid in users.items():
            accounts.configure(c, uid, 'draft.' + role, 'DraftFixture2026!', users['manager'])
        domains = [row[0] for row in c.execute("SELECT id FROM org_node WHERE node_type='domain' AND inactive_at IS NULL LIMIT 2")]
        assert len(domains) == 2
        approver = app.approver_for(c, users['member'])
        timestamp = app.now()
        tid = c.execute('INSERT INTO task(title,description,deliverable,owner_id,org_node_id,due_at,priority,approver_id,approval_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        ('待确认任务责任域回归测试', '原始任务详情\n保留链接 https://example.invalid/design', '保留原验收标准', users['member'], domains[0], '2030-11-13T09:30:00+08:00', 'P1', approver, 'pending', timestamp, timestamp)).lastrowid
        aid = c.execute("INSERT INTO approval_record(task_id,biz_id,biz_type,approver_id,status,created_at) VALUES(?,?,'task_confirm',?,'pending',?)", (tid, tid, approver, timestamp)).lastrowid
        c.commit()
        c.close()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fixture = Path(folder) / 'fixture.json'
        fixture.write_text(json.dumps({'base': f'http://127.0.0.1:{server.server_port}', 'task': tid, 'approval': aid, 'domains': domains, 'users': users}))
        try:
            subprocess.run(['node', 'tests/pending-drafts.browser.cjs'], cwd=ROOT,
                           env=dict(os.environ, WMT_PENDING_DRAFT_FIXTURE=str(fixture)), check=True, timeout=120)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
