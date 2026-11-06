import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
import app
import routines


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'FEISHU_APP_ID': '', 'FEISHU_APP_SECRET': '',
                                         'WECOM_CORP_ID': '', 'WECOM_SECRET': '',
                                         'AI_API_KEY': '', 'WMT_AUTH_MODE': 'demo'})
        self.env.start(); self.addCleanup(self.env.stop)
        db_patch = patch.object(app, 'DB_PATH', Path(self.temp.name) / 'routines.db')
        db_patch.start(); self.addCleanup(db_patch.stop)
        app.initialize()
        self.c = app.db(); self.addCleanup(self.c.close)
        self.owner = self.c.execute("SELECT id FROM user WHERE role='member'").fetchone()[0]
        self.manager = self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.other = self.c.execute("INSERT INTO user(name,role) VALUES('工时权限测试员工','member')").lastrowid
        self.c.commit()
        self.domain = self.c.execute("SELECT id FROM org_node WHERE node_type='domain'").fetchone()[0]

    def api(self, path, body, actor=None):
        h = object.__new__(app.Handler)
        h.path, h.headers, h.command = path, {}, 'POST'
        h.authenticated_user_id = self.manager if actor is None else actor
        h.require_auth = lambda: True
        h.body = lambda: body
        result = []
        h.json = lambda status, payload: result.append((status, payload))
        h.do_POST()
        return result[0]

    def payload(self, **changes):
        return dict(title='核心系统日常运维', owner_id=self.owner, org_node_id=self.domain,
                    deliverable='巡检、备份与异常处理记录', recurrence='WEEKLY', estimated_hours=16.5, **changes)

    def template(self):
        status, out = self.api('/api/routine/templates', self.payload())
        self.assertEqual(status, 200)
        return out['id']

    def generate(self, day='2026-11-09'):
        ids = app.generate_routines(self.c, day); self.c.commit(); return ids

    def test_template_hours_are_required_and_reject_invalid_values(self):
        for bad in (None, '', 0, -1, True, 'bad', float('nan'), float('inf'), 169):
            body = self.payload(); body['estimated_hours'] = bad
            self.assertEqual(self.api('/api/routine/templates', body)[0], 400, repr(bad))
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_template').fetchone()[0], 0)
        body = self.payload(); body['recurrence'] = 'DAILY'
        self.assertEqual(self.api('/api/routine/templates', body)[0], 400)

    def test_sunday_cutoff_china_time_and_year_boundary(self):
        china = timezone(timedelta(hours=8))
        self.assertEqual(routines.target_week(current_time=datetime(2026, 11, 8, 17, 59, tzinfo=china)).isoformat(), '2026-11-02')
        self.assertEqual(routines.target_week(current_time=datetime(2026, 11, 8, 10, tzinfo=timezone.utc)).isoformat(), '2026-11-09')
        self.assertEqual(routines.target_week(current_time=datetime(2026, 11, 9, 9, tzinfo=china)).isoformat(), '2026-11-09')
        self.assertEqual(routines.target_week(current_time=datetime(2023, 12, 31, 18, tzinfo=china)).isoformat(), '2024-01-01')

    def test_generation_copies_hours_period_and_is_immediately_formal(self):
        template = self.template()
        ids = app.generate_routines(self.c, current_time=datetime.fromisoformat('2026-11-08T18:00:00+08:00'))
        self.c.commit(); self.assertEqual(len(ids), 1)
        task = dict(self.c.execute('SELECT * FROM task WHERE id=?', (ids[0],)).fetchone())
        self.assertEqual(task['estimated_hours'], 16.5)
        self.assertEqual(task['actual_hours'], 0)
        self.assertEqual(task['routine_week_start'], '2026-11-09')
        self.assertEqual(task['routine_week_end'], '2026-11-15')
        self.assertEqual(task['due_at'], '2026-11-15T23:59:59+08:00')
        self.assertEqual(task['approval_status'], 'approved')
        self.assertEqual(task['work_status'], 'assigned')
        self.assertEqual(task['parent_template_id'], template)
        self.assertIsNone(task['routine_hours_updated_at'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM approval_record WHERE task_id=?', (ids[0],)).fetchone()[0], 0)

    def test_any_date_in_same_week_is_idempotent_and_next_week_is_independent(self):
        self.template(); self.assertEqual(len(self.generate()), 1)
        self.assertEqual(self.generate('2026-11-15'), [])
        self.assertEqual(len(self.generate('2026-11-16')), 1)

    def test_template_updates_only_affect_future_tasks(self):
        template = self.template(); first = self.generate()[0]
        body = self.payload(); body['estimated_hours'] = 20
        self.assertEqual(self.api('/api/routine/templates/' + str(template), body)[0], 200)
        self.assertEqual(self.generate(), [])
        second = self.generate('2026-11-16')[0]
        self.assertEqual(self.c.execute('SELECT estimated_hours FROM task WHERE id=?', (first,)).fetchone()[0], 16.5)
        self.assertEqual(self.c.execute('SELECT estimated_hours FROM task WHERE id=?', (second,)).fetchone()[0], 20)

    def test_legacy_unconfigured_template_is_visible_but_not_generated(self):
        self.c.execute("INSERT INTO task_template(title,owner_id,org_node_id,deliverable,recurrence,created_at) VALUES(?,?,?,?,?,?)",
                       ('旧模板', self.owner, self.domain, '记录', 'DAILY', app.now()))
        routines.migrate(self.c); self.c.commit()
        self.assertEqual(self.c.execute('SELECT recurrence FROM task_template').fetchone()[0], 'WEEKLY')
        self.assertEqual(self.generate(), [])

    def test_weekly_hours_replace_instead_of_accumulate_and_preserve_status(self):
        self.template(); tid = self.generate()[0]
        for amount in (18.5, 20, 0):
            status, out = self.api(f'/api/tasks/{tid}/hours', {'actual_hours': amount, 'operator_id': self.other}, self.owner)
            self.assertEqual(status, 200, out)
            task = self.c.execute('SELECT * FROM task WHERE id=?', (tid,)).fetchone()
            self.assertEqual(task['actual_hours'], amount)
            self.assertEqual(task['estimated_hours'], 16.5)
            self.assertEqual(task['work_status'], 'assigned')
            self.assertTrue(task['routine_hours_updated_at'])

    def test_hours_cannot_be_spoofed_and_invalid_values_are_atomic(self):
        self.template(); tid = self.generate()[0]
        self.assertEqual(self.api(f'/api/tasks/{tid}/hours', {'actual_hours': 10, 'operator_id': self.owner}, self.other)[0], 403)
        for bad in (None, '', -1, 169, True, 'NaN', 'Infinity'):
            self.assertEqual(self.api(f'/api/tasks/{tid}/hours', {'actual_hours': bad}, self.owner)[0], 400)
        self.assertEqual(self.c.execute('SELECT actual_hours FROM task WHERE id=?', (tid,)).fetchone()[0], 0)

    def test_competing_generation_only_creates_one_instance(self):
        self.template()
        def run(_):
            c = app.db()
            try:
                ids = app.generate_routines(c, '2026-11-09'); c.commit(); return ids
            finally:
                c.close()
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sum(len(ids) for ids in pool.map(run, range(3))), 1)

    def test_startup_does_not_rewrite_normalized_task_timestamps(self):
        stamp = '2026-10-01T09:00:00+08:00'
        tid = self.c.execute("INSERT INTO task(title,description,owner_id,due_at,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             ('系统接口维护', '系统接口维护', self.owner, '2026-11-09', 'conversation', stamp, stamp)).lastrowid
        app.normalize_legacy_conversation_tasks(self.c)
        first = dict(self.c.execute('SELECT * FROM task WHERE id=?', (tid,)).fetchone())
        app.normalize_legacy_conversation_tasks(self.c)
        self.assertEqual(dict(self.c.execute('SELECT * FROM task WHERE id=?', (tid,)).fetchone()), first)


if __name__ == '__main__':
    unittest.main()
