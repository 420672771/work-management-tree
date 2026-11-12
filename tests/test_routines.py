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
        return self.stored_template(self.payload())

    def stored_template(self, payload):
        # Generator tests start with an existing template before its first run.
        # Creation's immediate run is covered separately through the API below.
        fields=routines.template_fields(self.c,payload);fields['created_at']=app.now()
        tid=self.c.execute('INSERT INTO task_template('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
        self.c.commit();return tid

    def generate(self, day='2026-11-09'):
        ids = app.generate_routines(self.c, day); self.c.commit(); return ids

    def position(self, name='同岗巡检员'):
        dept=self.c.execute('SELECT id FROM org_department LIMIT 1').fetchone()[0]
        pid=self.c.execute('INSERT INTO org_position(name,dept_id) VALUES(?,?)',(name,dept)).lastrowid
        self.c.commit();return pid

    def assign(self, position, owner, start=None, end=None, primary=1):
        self.c.execute('INSERT INTO user_position(user_id,position_id,is_primary,valid_from,valid_to) VALUES(?,?,?,?,?)',
                       (owner,position,primary,start,end));self.c.commit()

    def people_template(self, owners):
        payload=self.payload();payload.pop('owner_id');payload['owner_ids']=owners
        return self.stored_template(payload)

    def test_creation_immediately_generates_current_calendar_week_only(self):
        old=self.template()
        for stamp,monday in [('2026-11-08T09:59:00+00:00','2026-11-02'),
                             ('2026-11-08T10:01:00+00:00','2026-11-02'),
                             ('2026-11-08T16:01:00+00:00','2026-11-09'),
                             ('2023-12-31T15:59:00+00:00','2023-12-25')]:
            with self.subTest(stamp=stamp),patch.object(app,'now',return_value=stamp):
                body=self.payload();body.pop('owner_id');body['owner_ids']=[self.owner,self.other,self.owner]
                status,out=self.api('/api/routine/templates',body)
                self.assertEqual(status,200,out);self.assertEqual(out['count'],2)
                tasks=[dict(self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()) for tid in out['generated_task_ids']]
                self.assertEqual({task['owner_id'] for task in tasks},{self.owner,self.other})
                self.assertTrue(all(task['routine_week_start']==monday and task['parent_template_id']==out['id'] and task['approval_status']=='approved' for task in tasks))
                self.assertTrue(all(task['estimated_hours']==16.5 for task in tasks))
                self.assertEqual(app.generate_routines(self.c,monday,template_ids=[out['id']]),[])
                self.c.commit()
                next_week=(datetime.fromisoformat(monday)+timedelta(days=7)).date().isoformat()
                later=app.generate_routines(self.c,next_week,template_ids=[out['id']]);self.c.commit()
                self.assertEqual(len(later),2)
        self.assertFalse(self.c.execute('SELECT 1 FROM task_instance WHERE template_id=?',(old,)).fetchone())

    def test_creation_uses_existing_scheduler_rule_after_the_immediate_run(self):
        with patch.object(app,'now',return_value='2026-11-08T10:01:00+00:00'):
            status,out=self.api('/api/routine/templates',self.payload())
        self.assertEqual(status,200)
        ids=app.generate_routines(self.c,current_time=datetime.fromisoformat('2026-11-08T18:02:00+08:00'),template_ids=[out['id']]);self.c.commit()
        self.assertEqual(len(ids),1)
        self.assertEqual(self.c.execute('SELECT routine_week_start FROM task WHERE id=?',(ids[0],)).fetchone()[0],'2026-11-09')
        self.assertEqual(self.c.execute('SELECT routine_week_start FROM task WHERE id=?',(out['generated_task_ids'][0],)).fetchone()[0],'2026-11-02')

    def test_generation_failure_rolls_back_template_and_audit(self):
        before=self.c.execute('SELECT count(*) FROM task_template').fetchone()[0]
        audit_count=self.c.execute('SELECT count(*) FROM audit_log').fetchone()[0]
        task_count=self.c.execute('SELECT count(*) FROM task').fetchone()[0]
        generate=app.generate_routines
        def fail_after_generation(*args,**kwargs):
            generate(*args,**kwargs)
            raise ValueError('生成失败，请重试')
        with patch.object(app,'generate_routines',side_effect=fail_after_generation):
            status,out=self.api('/api/routine/templates',self.payload())
        self.assertEqual(status,400);self.assertIn('生成失败',out['error'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_template').fetchone()[0],before)
        self.assertEqual(self.c.execute('SELECT count(*) FROM audit_log').fetchone()[0],audit_count)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],task_count)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_instance').fetchone()[0],0)

    def test_editing_template_does_not_regenerate_current_week_or_change_snapshots(self):
        with patch.object(app,'now',return_value='2026-11-12T00:00:00+00:00'):
            status,out=self.api('/api/routine/templates',self.payload())
        self.assertEqual(status,200);original=out['generated_task_ids'][0]
        body=self.payload();body.pop('owner_id');body.update(owner_ids=[self.owner,self.other],estimated_hours=20)
        status,edited=self.api('/api/routine/templates/'+str(out['id']),body)
        self.assertEqual(status,200);self.assertEqual(edited['count'],0)
        self.assertEqual(self.c.execute('SELECT estimated_hours FROM task WHERE id=?',(original,)).fetchone()[0],16.5)
        ids=app.generate_routines(self.c,'2026-11-12',template_ids=[out['id']]);self.c.commit()
        self.assertEqual(len(ids),1)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(ids[0],)).fetchone()[0],self.other)

    def test_multi_select_generates_independent_tasks_only_for_selected_people(self):
        position=self.position();self.assign(position,self.owner);self.assign(position,self.other)
        template=self.people_template([self.owner,self.other])
        ids=self.generate();self.assertEqual(len(ids),2)
        tasks=[dict(self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()) for tid in ids]
        self.assertEqual({t['owner_id'] for t in tasks},{self.owner,self.other})
        self.assertTrue(all(t['estimated_hours']==16.5 and t['routine_position_id'] is None for t in tasks))
        self.assertTrue(all(t['parent_template_id']==template for t in tasks))
        self.assertEqual(self.generate(),[])
        summary=routines.list_templates(self.c,'2026-11-09')[0]
        self.assertEqual(summary['target_count'],2);self.assertEqual(summary['owner_ids'],[self.owner,self.other])
        self.assertEqual(self.api(f'/api/tasks/{ids[0]}/hours',{'actual_hours':3},tasks[0]['owner_id'])[0],200)
        self.assertEqual(self.c.execute('SELECT actual_hours FROM task WHERE id=?',(ids[1],)).fetchone()[0],0)
        self.assertEqual(self.api(f'/api/tasks/{ids[0]}/hours',{'actual_hours':7},tasks[1]['owner_id'])[0],404)

    def test_same_position_can_have_different_routines_and_unselected_staff_are_excluded(self):
        position=self.position();self.assign(position,self.owner);self.assign(position,self.other)
        self.assign(position,self.manager)
        first=self.people_template([self.owner]);second=self.people_template([self.other])
        self.generate()
        self.assertEqual({r[0] for r in self.c.execute('SELECT owner_id FROM task WHERE parent_template_id=?',(first,))},{self.owner})
        self.assertEqual({r[0] for r in self.c.execute('SELECT owner_id FROM task WHERE parent_template_id=?',(second,))},{self.other})
        self.assertFalse(self.c.execute('SELECT 1 FROM task WHERE owner_id=?',(self.manager,)).fetchone())

    def test_repeated_employee_ids_are_deduplicated(self):
        template=self.people_template([self.owner,self.owner,self.other,self.owner])
        self.assertEqual(routines.list_templates(self.c)[0]['owner_ids'],[self.owner,self.other])
        self.assertEqual(len(self.generate()),2)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_instance WHERE template_id=?',(template,)).fetchone()[0],2)

    def test_people_added_can_be_backfilled_and_removal_only_affects_future_tasks(self):
        template=self.people_template([self.owner]);first=self.generate()[0]
        body=self.payload();body.pop('owner_id');body['owner_ids']=[self.owner,self.other]
        self.assertEqual(self.api('/api/routine/templates/'+str(template),body)[0],200)
        extra=self.generate();self.assertEqual(len(extra),1)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(extra[0],)).fetchone()[0],self.other)
        body['owner_ids']=[self.other]
        self.assertEqual(self.api('/api/routine/templates/'+str(template),body)[0],200)
        self.assertEqual(self.generate(),[])
        upcoming=self.generate('2026-11-16');self.assertEqual(len(upcoming),1)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(upcoming[0],)).fetchone()[0],self.other)
        self.assertIsNotNone(self.c.execute('SELECT 1 FROM task WHERE id=?',(first,)).fetchone())

    def test_existing_personal_template_can_select_more_people_without_duplicating_tasks(self):
        template=self.template();old=self.generate()[0]
        body=self.payload();body.pop('owner_id');body.update(owner_ids=[self.owner,self.other],estimated_hours=20)
        self.assertEqual(self.api('/api/routine/templates/'+str(template),body)[0],200)
        extra=self.generate();self.assertEqual(len(extra),1)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(extra[0],)).fetchone()[0],self.other)
        self.assertEqual(self.c.execute('SELECT estimated_hours FROM task WHERE id=?',(old,)).fetchone()[0],16.5)
        self.assertEqual(len(self.generate('2026-11-16')),2)

    def test_deleted_or_reassigned_task_is_not_regenerated_for_its_original_recipient(self):
        self.people_template([self.owner,self.other]);ids=self.generate()
        self.c.execute('UPDATE task SET owner_id=? WHERE id=?',(self.manager,ids[0]))
        self.c.execute('DELETE FROM task WHERE id=?',(ids[1],));self.c.commit()
        self.assertEqual(self.generate(),[])
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_instance').fetchone()[0],2)

    def test_concurrent_generation_is_unique_per_selected_employee(self):
        self.people_template([self.owner,self.other])
        def run(_):
            with app.db() as c:
                ids=app.generate_routines(c,'2026-11-09');c.commit();return ids
        with ThreadPoolExecutor(max_workers=3) as pool:self.assertEqual(sum(map(len,pool.map(run,range(3)))),2)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task_instance').fetchone()[0],2)

    def test_deactivated_templates_preserve_existing_tasks(self):
        template=self.people_template([self.owner]);original=self.generate()[0]
        self.assertEqual(self.api('/api/routine/templates/'+str(template),{'action':'deactivate'},self.owner)[0],403)
        self.assertEqual(self.api('/api/routine/templates/'+str(template),{'action':'deactivate'})[0],200)
        self.assertEqual(self.generate('2026-11-16'),[])
        self.assertIsNotNone(self.c.execute('SELECT 1 FROM task WHERE id=?',(original,)).fetchone())

    def test_people_selection_rejects_empty_invalid_and_position_based_targets(self):
        for invalid in ([],None,'1',[True],[-1],[999999],[self.owner,None]):
            body=self.payload();body.pop('owner_id');body['owner_ids']=invalid
            self.assertEqual(self.api('/api/routine/templates',body)[0],400,repr(invalid))
        for changes in ({'assignment_type':'position','position_id':self.position()},
                        {'position_id':999999},{'assignment_type':'unknown'},
                        {'owner_ids':[self.other]}):
            body=self.payload();body.update(changes)
            self.assertEqual(self.api('/api/routine/templates',body)[0],400,changes)

    def test_position_legacy_migration_freezes_employee_selection_once(self):
        position=self.position();self.assign(position,self.owner);self.assign(position,self.other)
        template=self.c.execute("INSERT INTO task_template(org_node_id,title,owner_id,deliverable,recurrence,created_at,estimated_hours,assignment_type,position_id) VALUES(?,? ,NULL,?,'WEEKLY',?,2,'position',?)",
            (self.domain,'旧岗位模板','记录',app.now(),position)).lastrowid
        self.c.commit();routines.migrate(self.c);self.c.commit()
        migrated=dict(self.c.execute('SELECT * FROM task_template WHERE id=?',(template,)).fetchone())
        self.assertEqual(migrated['assignment_type'],'people');self.assertIsNone(migrated['position_id'])
        self.assertEqual(routines.selected_owner_ids(migrated),[self.owner,self.other])
        self.assign(position,self.manager)
        self.c.execute('DELETE FROM user_position WHERE user_id=?',(self.owner,));self.c.commit()
        routines.migrate(self.c);self.c.commit()
        tasks=app.generate_routines(self.c,'2026-11-09',template_ids=[template]);self.c.commit()
        self.assertEqual({self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0] for tid in tasks},{self.owner,self.other})
        self.assertEqual(self.c.execute("SELECT count(*) FROM audit_log WHERE action='position_to_people'").fetchone()[0],1)

    def test_legacy_migration_preserves_ids_deleted_instances_and_autoincrement(self):
        self.c.executescript('''DROP TABLE task_instance; DROP TABLE task_template;
            CREATE TABLE task_template(id INTEGER PRIMARY KEY AUTOINCREMENT,org_node_id INTEGER NOT NULL,
             title TEXT NOT NULL,description TEXT DEFAULT '',owner_id INTEGER NOT NULL,deliverable TEXT NOT NULL,
             priority TEXT NOT NULL DEFAULT 'P1',recurrence TEXT NOT NULL,due_offset_days INTEGER NOT NULL DEFAULT 0,
             status TEXT NOT NULL DEFAULT 'active',review_at TEXT,created_at TEXT NOT NULL);
            CREATE TABLE task_instance(id INTEGER PRIMARY KEY AUTOINCREMENT,template_id INTEGER NOT NULL,
             instance_date TEXT NOT NULL,task_id INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(template_id,instance_date));''')
        self.c.execute('INSERT INTO task_template(id,org_node_id,title,owner_id,deliverable,recurrence,created_at) VALUES(41,?,?,?,?,?,?)',
                       (self.domain,'迁移模板',self.owner,'记录','WEEKLY',app.now()))
        self.c.execute('INSERT INTO task_instance(id,template_id,instance_date,task_id,created_at) VALUES(91,41,?,?,?)',('2026-11-09',99999,app.now()))
        self.c.execute("UPDATE sqlite_sequence SET seq=100 WHERE name='task_template'")
        self.c.execute("UPDATE sqlite_sequence SET seq=200 WHERE name='task_instance'");self.c.commit()
        routines.migrate(self.c);self.c.commit()
        template=dict(self.c.execute('SELECT * FROM task_template WHERE id=41').fetchone())
        self.assertEqual(template['owner_id'],self.owner);self.assertEqual(template['assignment_type'],'person')
        instance=dict(self.c.execute('SELECT * FROM task_instance WHERE id=91').fetchone())
        self.assertEqual(instance['owner_id'],self.owner);self.assertEqual(instance['task_id'],99999)
        routines.migrate(self.c);self.c.commit()
        self.assertEqual(dict(self.c.execute('SELECT * FROM task_instance WHERE id=91').fetchone()),instance)
        self.assertGreater(self.template(),100)
        self.generate('2026-11-16')
        self.assertGreater(self.c.execute('SELECT max(id) FROM task_instance').fetchone()[0],200)

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
        self.assertEqual(self.api(f'/api/tasks/{tid}/hours', {'actual_hours': 10, 'operator_id': self.owner}, self.other)[0], 404)
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

    def test_employee_cannot_create_edit_or_manually_generate_templates(self):
        template = self.template()
        body = {**self.payload(), 'actor_id': self.manager}
        for path in ('/api/routine/templates','/api/routine/templates/'+str(template)):
            status,error=self.api(path,body,self.owner)
            self.assertEqual(status,403);self.assertIn('例行任务模板',error['error'])
            self.assertNotIn('密码',error['error'])
        status,error=self.api('/api/routine/generate',{'actor_id':self.manager},self.owner)
        self.assertEqual(status,403);self.assertIn('补生成例行任务',error['error'])

    def test_scoped_generation_does_not_touch_other_templates(self):
        first = self.template(); second = self.template()
        ids = app.generate_routines(self.c, '2026-11-09', template_ids=[second]); self.c.commit()
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.c.execute('SELECT parent_template_id FROM task WHERE id=?', (ids[0],)).fetchone()[0], second)
        self.assertFalse(self.c.execute('SELECT 1 FROM task_instance WHERE template_id=?', (first,)).fetchone())

    def test_generated_standard_hours_are_frozen_but_weekly_reporting_counts_as_process(self):
        self.template(); tid = self.generate()[0]
        self.assertEqual(self.api(f'/api/tasks/{tid}', {'estimated_hours': 100}, self.owner)[0], 400)
        self.assertEqual(self.api(f'/api/tasks/{tid}/hours', {'actual_hours': 0}, self.owner)[0], 200)
        task = dict(self.c.execute('SELECT * FROM task WHERE id=?', (tid,)).fetchone())
        result = app.task_performance(self.c, task)
        self.assertTrue(result['process_recorded'])
        self.assertEqual(result['estimated_hours'], 16.5)

    def test_100_percent_routine_progress_waits_for_acceptance(self):
        self.template(); tid = self.generate()[0]
        status, progress = self.api(f'/api/tasks/{tid}/progress', {'percent': 100, 'comment': '交付物已提交'}, self.owner)
        self.assertEqual(status, 200)
        self.assertEqual(self.api(f"/api/approvals/{progress['approval_id']}/confirm", {'result': 'approved'})[0], 200)
        self.assertEqual(self.c.execute('SELECT work_status FROM task WHERE id=?', (tid,)).fetchone()[0], 'awaiting_acceptance')

    def test_multiple_pms_do_not_recurse_in_management_performance(self):
        self.c.execute("INSERT INTO user(name,role) VALUES('第二位管理PM','pm')"); self.c.commit()
        pm = self.c.execute("SELECT id FROM user WHERE role='pm' LIMIT 1").fetchone()[0]
        report = app.performance_summary(self.c, pm, '2026-11')
        self.assertIn('management', report)


if __name__ == '__main__':
    unittest.main()
