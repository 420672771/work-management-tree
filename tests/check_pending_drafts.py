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
    browser_test=sys.argv[1] if len(sys.argv)>1 else 'tests/pending-drafts.browser.cjs'
    environment = {'WMT_AUTH_MODE': 'account', 'WMT_MONTHLY_DEMO': '0'}
    environment.update({key: '' for key in os.environ if key.startswith(('AI_', 'FEISHU_', 'WECOM_'))})
    with tempfile.TemporaryDirectory(prefix='wmt-pending-drafts-') as folder, patch.dict(os.environ, environment), patch.object(app, 'DB_PATH', Path(folder) / 'fixture.db'):
        app.initialize()
        c = app.db()
        users = {role: c.execute('SELECT id FROM user WHERE role=? LIMIT 1', (role,)).fetchone()[0]
                 for role in ('manager', 'pm', 'member')}
        users['other'] = c.execute("SELECT id FROM user WHERE role='member' AND id!=? LIMIT 1", (users['member'],)).fetchone()[0]
        if browser_test.endswith('task-visibility.browser.cjs'):
            users['leader']=c.execute("INSERT INTO user(name,role,department) VALUES('团队负责人示例','member','科技产品中心')").lastrowid
            c.execute('UPDATE reporting_line SET direct_leader_id=? WHERE user_id=?',(users['leader'],users['member']))
        for role, uid in users.items():
            accounts.configure(c, uid, 'draft.' + role, 'DraftFixture2026!', users['manager'])
        domains = [row[0] for row in c.execute("SELECT id FROM org_node WHERE node_type='domain' AND inactive_at IS NULL LIMIT 2")]
        assert len(domains) == 2
        approver = app.approver_for(c, users['member'])
        timestamp = app.now()
        tid = c.execute('INSERT INTO task(title,description,deliverable,owner_id,org_node_id,due_at,priority,approver_id,approval_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        ('待确认任务责任域回归测试', '原始任务详情\n保留链接 https://example.invalid/design', '保留原验收标准', users['member'], domains[0], '2030-11-13T09:30:00+08:00', 'P1', approver, 'pending', timestamp, timestamp)).lastrowid
        aid = c.execute("INSERT INTO approval_record(task_id,biz_id,biz_type,approver_id,status,created_at) VALUES(?,?,'task_confirm',?,'pending',?)", (tid, tid, approver, timestamp)).lastrowid
        confirmations = {}
        for key, owner, title in [('pm_self', users['pm'], 'PM本人确认测试'),
                                  ('pm_superior', users['pm'], 'PM上级确认测试'),
                                  ('manager_self', users['manager'], '负责人本人确认测试')]:
            assigned = app.approver_for(c, owner)
            task_id = c.execute('INSERT INTO task(title,owner_id,org_node_id,due_at,priority,approver_id,approval_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                (title, owner, domains[0], '2030-11-20T17:00:00+08:00', 'P1', assigned, 'pending', timestamp, timestamp)).lastrowid
            app.create_approval(c, 'task_confirm', task_id, task_id, app.task_confirmation_approver(c, owner, assigned))
            confirmations[key] = task_id
        visibility={}
        if 'leader' in users:
            from datetime import datetime,timedelta,timezone
            for key,owner,title in [('own',users['member'],'成员正式任务示例'),('private',users['pm'],'管理PM正式任务示例')]:
                task_id=c.execute("INSERT INTO task(title,owner_id,org_node_id,due_at,approval_status,work_status,approver_id,created_at,updated_at) VALUES(?,?,?,?,'approved','in_progress',?,?,?)",
                                  (title,owner,domains[0],'2030-11-20T17:00:00+08:00',app.approver_for(c,owner),timestamp,timestamp)).lastrowid
                visibility[key]=task_id
                slots={'title':title+'草稿','owner_id':owner,'due_at':'2030-11-20T17:00:00+08:00'}
                c.execute("INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,'text',?,?,'[]','preview',?,?,?)",
                          ('visibility-'+key,owner,title+'草稿',json.dumps(slots),(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),timestamp,timestamp))
        c.commit()
        c.close()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fixture = Path(folder) / 'fixture.json'
        fixture.write_text(json.dumps({'base': f'http://127.0.0.1:{server.server_port}', 'task': tid, 'approval': aid, 'domains': domains, 'users': users, 'confirmations': confirmations, 'visibility': visibility}))
        try:
            subprocess.run(['node', browser_test], cwd=ROOT,
                           env=dict(os.environ, WMT_PENDING_DRAFT_FIXTURE=str(fixture)), check=True, timeout=120)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
