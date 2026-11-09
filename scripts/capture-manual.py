#!/usr/bin/env python3
"""Capture handbook screenshots using fictional data in a disposable database.

Requires Playwright and Chromium. Set WMT_PLAYWRIGHT_MODULE if installed outside
the project. Never connects to the running site or its business database.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import accounts
import routines
import weekly_reports


def main():
    with tempfile.TemporaryDirectory(prefix='wmt-handbook-') as folder:
        database = Path(folder) / 'manual.db'
        with patch.object(app, 'DB_PATH', database):
            app.initialize()
            c = app.db()
            manager = c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
            member = c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]
            pm = c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
            for uid, name in ((manager, '王主管'), (member, '张三'), (pm, '陈经理')):
                c.execute('UPDATE user SET name=? WHERE id=?', (name, uid))
            # Default responsibility initialization can create named owners.
            # Keep only the fictional actors used by this handbook fixture.
            c.execute('UPDATE org_node SET owner_id=?', (manager,))
            c.execute('DELETE FROM reporting_line WHERE user_id NOT IN (?,?,?)', (manager, member, pm))
            c.execute('DELETE FROM user WHERE id NOT IN (?,?,?)', (manager, member, pm))
            other = c.execute("INSERT INTO user(name,department,role) VALUES('李四','科技产品中心','member')").lastrowid
            c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)',
                      (other, manager, manager, app.now()))
            for uid, login in ((manager, 'manual.manager'), (member, 'zhangsan')):
                accounts.configure(c, uid, login, '123456', manager)
            department = c.execute('SELECT id FROM org_department LIMIT 1').fetchone()[0]
            position = c.execute("INSERT INTO org_position(name,dept_id) VALUES('应用工程师',?)", (department,)).lastrowid
            for uid in (member, other):
                c.execute('INSERT INTO user_position(user_id,position_id) VALUES(?,?)', (uid, position))
            domain = c.execute("SELECT id FROM org_node WHERE domain_code='A1'").fetchone()[0]
            c.execute('UPDATE org_node SET owner_id=? WHERE node_type=?', (manager, 'domain'))
            today = weekly_reports.local_time().date()
            month = today.strftime('%Y-%m')
            stamp = today.isoformat() + 'T09:00:00+08:00'
            due = today.replace(day=28).isoformat() + 'T18:00:00+08:00'
            tasks = {}
            for key, title, status in (
                ('active', '完成客户服务流程评审', 'in_progress'),
                ('waiting', '交付知识库使用指引', 'awaiting_acceptance'),
                ('done', '完成采购审批需求梳理', 'completed'),
                ('pending', '整理业务试用反馈清单', 'draft'),
            ):
                fields = dict(title=title, description='核对业务流程和异常场景，组织相关人员评审并记录结论。',
                              owner_id=member, approver_id=manager, org_node_id=domain, due_at=due,
                              created_at=stamp, updated_at=stamp, work_status=status,
                              approval_status='pending' if key == 'pending' else 'approved',
                              deliverable='评审纪要、问题清单和下一步安排', priority='P0',
                              estimated_hours=8, actual_hours=6, source='handbook-fictional')
                tid = c.execute('INSERT INTO task('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',
                                tuple(fields.values())).lastrowid
                tasks[key] = tid
                if key == 'pending':app.create_approval(c, 'task_confirm', tid, tid, manager)
                if key == 'done':c.execute("INSERT INTO acceptance(task_id,acceptor_id,result,created_at) VALUES(?,?,'passed',?)", (tid, manager, stamp))
            fields = routines.template_fields(c, dict(title='每周知识库维护', org_node_id=domain,
                owner_ids=[member, other], deliverable='更新记录、问题汇总与维护清单', estimated_hours=2,
                recurrence='WEEKLY'))
            fields['created_at'] = stamp
            template = c.execute('INSERT INTO task_template('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',
                                 tuple(fields.values())).lastrowid
            c.commit()
            generated = app.generate_routines(c, today.isoformat(), template_ids=[template])
            routine = c.execute('SELECT id FROM task WHERE id IN ('+','.join('?' for _ in generated)+') AND owner_id=?', [*generated, member]).fetchone()[0]
            weekly_reports.generate_report(c, manager, today.isoformat())
            c.commit()
            c.close()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        fixture = Path(folder) / 'fixture.json'
        fixture.write_text(json.dumps(dict(base=base, manager=manager, member=member, other=other,
            domain=domain, tasks=tasks, routine=routine, template=template, month=month)), encoding='utf-8')
        env = dict(os.environ, WMT_DB=str(database), WMT_AUTH_MODE='account', WMT_MONTHLY_DEMO='0',
                   WMT_RELOAD_CHILD='1', PORT=str(port), WMT_MANUAL_FIXTURE=str(fixture))
        for key in list(env):
            if key.startswith(('FEISHU_', 'WECOM_', 'AI_')) or key in ('WMT_PUBLIC_USER', 'WMT_PUBLIC_PASSWORD', 'WMT_PUBLIC_BASE_URL', 'WMT_FEISHU_OAUTH_REDIRECT_URI'):
                env.pop(key)
        with (Path(folder) / 'server.log').open('w') as log:
            server = subprocess.Popen([sys.executable, '-B', 'app.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base+'/login', timeout=1):break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('Isolated handbook server did not start')
                subprocess.run(['node', 'scripts/capture-manual.cjs'], cwd=ROOT, env=env, check=True, timeout=240)
            finally:
                server.terminate()
                try:server.wait(timeout=5)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__ == '__main__':
    main()
