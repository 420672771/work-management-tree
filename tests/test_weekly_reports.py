import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
import app
import weekly_reports as reports


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(app, 'DB_PATH', Path(self.temp.name) / 'weekly.db')
        self.db_patch.start()
        app.initialize()
        self.c = app.db()
        self.manager = self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm = self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member = self.c.execute("SELECT id FROM user WHERE role='member'").fetchone()[0]
        self.domain = self.c.execute("SELECT id FROM org_node WHERE node_type='domain'").fetchone()[0]
        self.generated = '2026-11-08T18:00:00+08:00'

    def tearDown(self):
        self.c.close()
        self.db_patch.stop()
        self.temp.cleanup()

    def task(self, **changes):
        fields = dict(title='采购审批验证', owner_id=self.member, org_node_id=self.domain,
                      due_at='2026-11-10T17:00:00+08:00', priority='P1', work_status='in_progress',
                      deliverable='验证记录', approval_status='approved', estimated_hours=8, actual_hours=10,
                      created_at='2026-11-02T10:00:00+08:00', updated_at='2026-11-06T10:00:00+08:00')
        fields.update(changes)
        task_id = self.c.execute('INSERT INTO task ('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', tuple(fields.values())).lastrowid
        self.c.commit()
        return task_id

    def generate(self):
        result = reports.generate_report(self.c, self.manager, '2026-11-04', self.generated)
        self.c.commit()
        return result

    def api(self, path, method='GET', body=None, actor=None):
        # Drive the real routing and authorization without opening a socket.
        handler = object.__new__(app.Handler)
        handler.path, handler.command, handler.headers = path, method, {}
        handler.authenticated_user_id = actor if actor is not None else self.manager
        handler.require_auth = lambda: True
        handler.body = lambda: dict(body or {})
        output = []
        handler.json = lambda status, payload: output.append((status, payload))
        (handler.do_GET if method == 'GET' else handler.do_POST)()
        return output[0]

    def test_filters_formal_department_tasks_and_freezes_snapshot_after_changes(self):
        kept = self.task()
        self.task(title='待确认', approval_status='pending')
        self.task(title='草稿', work_status='draft')
        self.task(title='已取消', work_status='cancelled')
        self.task(title='未来创建', created_at='2026-11-08T19:00:00+08:00')
        self.task(title='未来才确认', confirmed_at='2026-11-08T19:00:00+08:00')
        external = self.c.execute("INSERT INTO org_node(node_type,name,created_at) VALUES('department','其他部门',?)", (app.now(),)).lastrowid
        self.c.commit()
        self.task(title='其他部门', org_node_id=external)
        report = self.generate()
        self.assertEqual([t['id'] for t in report['snapshot']['tasks']], [kept])
        self.c.execute('DELETE FROM task WHERE id=?', (kept,)); self.c.commit()
        self.assertEqual(reports.get_report(self.c, report['id'], self.pm)['snapshot'], report['snapshot'])
        self.assertIn('采购审批验证', reports.meeting_text(reports.get_report(self.c, report['id'], self.pm)))

    def test_metrics_completion_event_time_and_hours_do_not_use_last_edit(self):
        done = self.task(work_status='completed', updated_at='2026-11-20T10:00:00+08:00')
        self.c.execute("INSERT INTO acceptance(task_id,result,created_at) VALUES(?,'passed',?)", (done, '2026-11-06T10:00:00+08:00'))
        self.c.commit()
        old = self.task(work_status='completed')
        self.c.execute("INSERT INTO acceptance(task_id,result,created_at) VALUES(?,'passed',?)", (old, '2026-10-30T10:00:00+08:00'))
        self.c.commit()
        late = self.task(due_at='2026-11-07T17:00:00+08:00')
        waiting = self.task(work_status='awaiting_acceptance', updated_at='2026-11-05T10:00:00+08:00')
        report = self.generate(); snapshot = report['snapshot']
        self.assertEqual(snapshot['metric_tasks']['completed'], [done])
        self.assertEqual(snapshot['metrics']['completed_hours'], {'standard': 8.0, 'actual': 10.0})
        self.assertEqual(snapshot['metric_tasks']['overdue'], [late])
        self.assertEqual(snapshot['metric_tasks']['awaiting_acceptance'], [waiting])
        self.assertEqual(snapshot['sections']['achievements'], [done])
        item = next(t for t in snapshot['tasks'] if t['id'] == waiting)
        self.assertIn('待验收超过 48 小时', item['risk_reasons'])

    def test_china_week_boundaries_and_snapshot_cutoff(self):
        monday = self.task(created_at='2026-11-01T16:00:00+00:00')
        sunday = self.task(created_at='2026-11-08T09:59:59+00:00')
        self.task(created_at='2026-11-08T10:00:01+00:00')
        late_progress = self.task()
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,report_text,reported_at) VALUES(?,?,?,?)',
                       (late_progress, 80, '生成后的更新', '2026-11-08T18:01:00+08:00'))
        self.c.commit()
        r = self.generate()
        self.assertEqual((r['period_start'], r['period_end']), ('2026-11-02', '2026-11-08'))
        self.assertIn(monday, r['snapshot']['metric_tasks']['new'])
        self.assertIn(sunday, r['snapshot']['metric_tasks']['new'])
        self.assertEqual(len(r['snapshot']['tasks']), 3)
        self.assertEqual(next(t for t in r['snapshot']['tasks'] if t['id'] == late_progress)['latest_progress'], '')

    def test_card_progress_uses_confirmed_percentage_instead_of_pending_report(self):
        tid=self.task(effective_percent=30)
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,report_text,status,effective_percent,reported_at,confirmed_at) VALUES(?,?,?,?,?,?,?)',
                       (tid,30,'已确认的进展','approved',30,'2026-11-05T10:00:00+08:00','2026-11-05T11:00:00+08:00'))
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,report_text,status,reported_at) VALUES(?,?,?,?,?)',
                       (tid,80,'填报80%，尚待确认','pending','2026-11-07T10:00:00+08:00'));self.c.commit()
        task=self.generate()['snapshot']['tasks'][0]
        self.assertEqual(task['effective_percent'],30);self.assertEqual(task['latest_progress'],'填报80%，尚待确认')

    def test_card_progress_excludes_confirmation_after_snapshot_cutoff(self):
        tid=self.task(effective_percent=90,updated_at='2026-11-09T10:00:00+08:00')
        for percent,reported,confirmed in ((20,'2026-11-04T10:00:00+08:00','2026-11-04T11:00:00+08:00'),
                                           (90,'2026-11-07T10:00:00+08:00','2026-11-09T10:00:00+08:00')):
            self.c.execute('INSERT INTO progress_record(task_id,reported_percent,report_text,status,effective_percent,reported_at,confirmed_at) VALUES(?,?,?,?,?,?,?)',
                           (tid,percent,'进展'+str(percent),'approved',percent,reported,confirmed))
        self.c.commit();report=self.generate();self.assertEqual(report['snapshot']['tasks'][0]['effective_percent'],20)
        self.c.execute('UPDATE task SET effective_percent=100 WHERE id=?',(tid,));self.c.commit()
        self.assertEqual(reports.get_report(self.c,report['id'],self.manager)['snapshot'],report['snapshot'])

    def test_card_progress_does_not_borrow_live_percentage_when_earlier_progress_is_unknown(self):
        tid=self.task(effective_percent=75,updated_at='2026-11-09T10:00:00+08:00')
        self.assertIsNone(self.generate()['snapshot']['tasks'][0]['effective_percent'])

    def legacy_report(self,tid):
        report=self.generate();snapshot=report['snapshot']
        next(task for task in snapshot['tasks'] if task['id']==tid).pop('effective_percent',None)
        self.c.execute('UPDATE weekly_report SET snapshot_json=? WHERE id=?',(json.dumps(snapshot,ensure_ascii=False),report['id']));self.c.commit()
        return report['id']

    def test_legacy_report_recovers_confirmed_progress_without_writing_frozen_data(self):
        tid=self.task(effective_percent=80,updated_at='2026-11-05T10:00:00+08:00')
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,effective_percent,report_text,status,reported_at,confirmed_at) VALUES(?,?,?,?,?,?,?)',
                       (tid,80,80,'此任务完成80%','auto_approved','2026-11-04T09:00:00+00:00','2026-11-05T02:00:00+00:00'));self.c.commit()
        rid=self.legacy_report(tid)
        for state in ('pending','published'):
            self.c.execute('UPDATE weekly_report SET status=? WHERE id=?',(state,rid));self.c.commit()
            before=dict(self.c.execute('SELECT * FROM weekly_report WHERE id=?',(rid,)).fetchone())
            status,response=self.api('/api/weekly-reports/'+str(rid))
            self.assertEqual(status,200);self.assertEqual(response['task_progress'][str(tid)],80)
            self.assertNotIn('effective_percent',response['snapshot']['tasks'][0])
            self.assertEqual(dict(self.c.execute('SELECT * FROM weekly_report WHERE id=?',(rid,)).fetchone()),before)

    def test_legacy_report_does_not_infer_progress_from_pending_text_or_future_confirmation(self):
        pending=self.task(effective_percent=90,updated_at='2026-11-09T10:00:00+08:00')
        future=self.task(effective_percent=95,updated_at='2026-11-09T10:00:00+08:00')
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,report_text,status,reported_at) VALUES(?,?,?,?,?)',
                       (pending,80,'此任务完成80%','pending','2026-11-07T10:00:00+08:00'))
        for percent,confirmed in ((30,'2026-11-05T10:00:00+08:00'),(95,'2026-11-09T10:00:00+08:00')):
            self.c.execute('INSERT INTO progress_record(task_id,reported_percent,effective_percent,report_text,status,reported_at,confirmed_at) VALUES(?,?,?,?,?,?,?)',
                           (future,percent,percent,'进展','approved','2026-11-04T10:00:00+08:00',confirmed))
        self.c.commit();rid=self.legacy_report(pending)
        row=self.c.execute('SELECT snapshot_json FROM weekly_report WHERE id=?',(rid,)).fetchone();snapshot=json.loads(row[0])
        for task in snapshot['tasks']:task.pop('effective_percent',None)
        self.c.execute('UPDATE weekly_report SET snapshot_json=? WHERE id=?',(json.dumps(snapshot),rid));self.c.commit()
        response=self.api('/api/weekly-reports/'+str(rid))[1]
        self.assertIsNone(response['task_progress'][str(pending)]);self.assertEqual(response['task_progress'][str(future)],30)

    def test_current_week_endpoint_recovers_legacy_progress_and_does_not_refresh_report(self):
        tid=self.task(effective_percent=80)
        self.c.execute('INSERT INTO progress_record(task_id,reported_percent,effective_percent,report_text,status,reported_at,confirmed_at) VALUES(?,?,?,?,?,?,?)',
                       (tid,80,80,'此任务完成80%','approved','2026-11-04T10:00:00+08:00','2026-11-05T10:00:00+08:00'));self.c.commit()
        rid=self.legacy_report(tid);before=dict(self.c.execute('SELECT * FROM weekly_report WHERE id=?',(rid,)).fetchone())
        with patch.object(reports,'local_time',side_effect=lambda value=None:reports.datetime.fromisoformat(self.generated) if value is None else reports.datetime.fromisoformat(value).astimezone(reports.CHINA)):
            response=self.api('/api/weekly-reports/current','POST',{})[1]
        self.assertEqual(response['id'],rid);self.assertEqual(response['task_progress'][str(tid)],80)
        self.assertEqual(dict(self.c.execute('SELECT * FROM weekly_report WHERE id=?',(rid,)).fetchone()),before)

    def test_key_work_includes_active_p0_and_all_weekly_completions_only(self):
        active = [self.task(priority='P0',work_status=status,estimated_hours=0,complexity='simple')
                  for status in ('assigned','in_progress','awaiting_acceptance')]
        completed = [self.task(priority=priority,work_status='completed',estimated_hours=0,complexity='simple')
                     for priority in ('P0','P1','P2')]
        # Neither high hours nor high complexity qualifies an unfinished P1/P2.
        excluded = [self.task(priority=priority,estimated_hours=100,complexity='major')
                    for priority in ('P1','P2')]
        excluded += [self.task(priority='P0',approval_status='pending'),
                     self.task(priority='P0',work_status='cancelled')]
        for priority in ('P0','P1','P2'):
            excluded.append(self.task(priority=priority,work_status='completed',updated_at='2026-10-30T10:00:00+08:00'))
        report=self.generate()
        self.assertEqual(set(report['snapshot']['sections']['achievements']),set(active+completed))
        self.assertTrue(set(report['snapshot']['sections']['achievements']).isdisjoint(excluded))
        self.assertEqual(report['snapshot']['metrics']['completed'],3)

    def test_default_section_and_metric_orders_use_numeric_task_ids(self):
        other=self.c.execute("SELECT id FROM org_node WHERE node_type='domain' AND id<>? LIMIT 1",(self.domain,)).fetchone()[0]
        for tid,node,due in ((30,other,'2026-11-09'),(2,self.domain,'2026-11-12'),(18,other,'2026-11-10')):
            self.task(id=tid,org_node_id=node,title='编号排序 '+str(tid),priority='P0',work_status='awaiting_acceptance',due_at=due+'T17:00:00+08:00')
        report=self.generate()
        for key in ('achievements','risks','next_week'):
            self.assertEqual(report['snapshot']['sections'][key],[2,18,30],key)
        for key in ('new','awaiting_acceptance'):
            self.assertEqual(report['snapshot']['metric_tasks'][key],[2,18,30],key)

    def test_legacy_export_sorts_by_id_without_domain_headings_and_preserves_custom_edits(self):
        import re
        for tid in (30,2,18):self.task(id=tid,title='编号排序 '+str(tid),priority='P0')
        report=self.generate()
        report['snapshot']['sections']['achievements']=[30,2,18]
        before=json.loads(json.dumps(report))
        def ids(value):
            section=reports.meeting_text(value).split('二、本周重点工作与成果\n')[1].split('\n三、任务进展与风险')[0]
            return [int(x) for x in re.findall(r'^- #(\d+) ',section,re.M)]
        self.assertEqual(ids(report),[2,18,30])
        text=reports.meeting_text(report)
        for task in report['snapshot']['tasks']:self.assertNotIn('\n'+task['domain_name']+'\n',text)
        self.assertEqual(report,before)
        report['editorial']['orders']['achievements']=[18,30,2]
        report['editorial']['notes']['18']='人工补充仍然保留'
        self.assertEqual(ids(report),[18,30,2]);self.assertIn('人工补充仍然保留',reports.meeting_text(report))

    def test_refresh_without_manual_sort_keeps_numeric_default_for_new_tasks(self):
        for tid in (30,18):self.task(id=tid,priority='P0')
        report=self.generate();self.task(id=5,priority='P0')
        refreshed=reports.refresh_report(self.c,report['id'],self.manager,0,refreshed_at=self.generated);self.c.commit()
        self.assertEqual(refreshed['snapshot']['sections']['achievements'],[5,18,30])
        self.assertEqual(refreshed['editorial']['orders'],{})

    def test_risks_next_week_and_weekly_progress(self):
        current = self.task()
        self.c.execute('''INSERT INTO progress_record(task_id,reported_percent,report_text,is_blocked,block_reason,reported_at)
                          VALUES(?,60,'验证中',1,'等待业务数据',?)''', (current, '2026-11-06T11:00:00+08:00'))
        self.c.commit()
        no_progress = self.task(priority='P0', due_at='2026-11-09T17:00:00+08:00')
        routine = self.task(priority='P2')
        r = self.generate(); tasks = {t['id']: t for t in r['snapshot']['tasks']}
        self.assertNotIn('本周无进展填报', tasks[current]['risk_reasons'])
        self.assertEqual(tasks[current]['block_reason'], '等待业务数据')
        self.assertIn('本周无进展填报', tasks[no_progress]['risk_reasons'])
        self.assertEqual(r['snapshot']['sections']['next_week'], [current, no_progress])
        self.assertNotIn(routine, r['snapshot']['sections']['risks'])

    def test_manager_publish_pm_edit_and_snapshot_numbers_cannot_change(self):
        task = self.task(); r = self.generate()
        for action in (lambda: reports.get_report(self.c, r['id'], self.member),
                       lambda: reports.list_reports(self.c, self.member),
                       lambda: reports.generate_report(self.c, self.pm),
                       lambda: reports.update_report(self.c, r['id'], self.pm, {'action':'publish','edit_revision':0})):
            with self.assertRaises(PermissionError): action()
        with self.assertRaises(ValueError):
            reports.update_report(self.c, r['id'], self.pm, {'edit_revision':0,'metrics':{'new':999}})
        saved = reports.update_report(self.c, r['id'], self.pm, {'edit_revision':0, 'summary':'本周重点推进采购审批验证', 'support':'需要业务支持', 'notes':{str(task):'周会确认数据准备情况'}})
        self.c.commit()
        self.assertEqual(saved['status'], 'pending')
        self.assertEqual(saved['snapshot'], r['snapshot'])
        published = reports.update_report(self.c, r['id'], self.manager, {'action':'publish','edit_revision':1})
        self.c.commit()
        self.assertEqual(published['status'], 'published')
        self.assertIsNotNone(published['publisher_name'])
        self.assertIn('需要业务支持', reports.meeting_text(published))
        with self.assertRaises(reports.ReportConflict):
            reports.update_report(self.c, r['id'], self.manager, {'edit_revision':2,'conclusion':'不能直接改历史'})

    def test_regeneration_preserves_published_version_and_creates_revision(self):
        self.task(); original = self.generate()
        published = reports.update_report(self.c, original['id'], self.manager, {'action':'publish','edit_revision':0,'conclusion':'历史结论'})
        self.c.commit()
        self.task(title='新增正式任务')
        new = self.generate()
        self.assertEqual(new['version'], 2)
        self.assertEqual(new['revision_of'], original['id'])
        self.assertEqual(new['editorial']['conclusion'], '历史结论')
        self.assertEqual(new['snapshot']['metrics']['new'], 2)
        self.assertEqual(reports.get_report(self.c, original['id'], self.manager), published)
        newer = self.generate()
        self.assertEqual(newer['version'], 3)
        self.assertEqual(reports.get_report(self.c, new['id'], self.pm)['status'], 'superseded')

    def test_conflicting_edits_and_order_validation(self):
        first=self.task(); second=self.task(); r=self.generate()
        ids=r['snapshot']['sections']['next_week']
        saved=reports.update_report(self.c,r['id'],self.pm,{'edit_revision':0,'orders':{'next_week':list(reversed(ids))}})
        self.c.commit()
        self.assertEqual(saved['editorial']['orders']['next_week'], [second,first])
        with self.assertRaises(reports.ReportConflict):
            reports.update_report(self.c,r['id'],self.manager,{'edit_revision':0,'summary':'过期编辑'})
        for orders in ({'next_week':[first]}, {'next_week':[first,first]}, {'unknown':[]}):
            with self.assertRaises(ValueError):
                reports.update_report(self.c,r['id'],self.manager,{'edit_revision':1,'orders':orders})

    def test_automatic_generation_idempotency_and_monday_catchup(self):
        self.task()
        before=reports.run_due(self.c,'2026-11-08T17:59:00+08:00');self.c.commit()
        self.assertEqual(before['period_start'],'2026-10-26')
        first=reports.run_due(self.c,self.generated);self.c.commit()
        r=reports.get_report(self.c,first['report_id'],self.manager)
        reports.update_report(self.c,r['id'],self.pm,{'edit_revision':0,'summary':'人工审阅内容'});self.c.commit()
        self.assertEqual(reports.run_due(self.c,'2026-11-08T20:00:00+08:00'),{'generated':0})
        self.assertEqual(reports.run_due(self.c,'2026-11-09T09:00:00+08:00'),{'generated':0})
        self.assertEqual(reports.get_report(self.c,r['id'],self.manager)['editorial']['summary'],'人工审阅内容')
        following=reports.run_due(self.c,'2026-11-16T09:00:00+08:00');self.c.commit()
        self.assertEqual(following['period_start'],'2026-11-09')

    def test_manual_draft_not_replaced_by_scheduled_job(self):
        r=self.generate()
        scheduled=reports.run_due(self.c,self.generated);self.c.commit()
        self.assertEqual(scheduled['report_id'],r['id'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM weekly_report').fetchone()[0],1)

    def test_concurrent_scheduled_jobs_create_one_snapshot(self):
        self.task()
        barrier=threading.Barrier(2)
        def run():
            c=app.db()
            try:
                barrier.wait(timeout=5)
                result=reports.run_due(c,self.generated);c.commit()
                return result['report_id']
            finally:c.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run) for _ in range(2)]
            ids=[f.result(timeout=15) for f in futures]
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(self.c.execute('SELECT count(*) FROM weekly_report').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM weekly_report_job').fetchone()[0],1)

    def test_mixed_timezones_use_latest_progress_and_confirmed_completion(self):
        task=self.task(work_status='completed')
        self.c.execute('''INSERT INTO progress_record(task_id,reported_percent,report_text,status,effective_percent,reported_at,confirmed_at)
                          VALUES(?,100,'完成确认','approved',100,?,?)''',
                       (task,'2026-11-08T09:00:00+00:00','2026-11-08T09:30:00+00:00'))
        self.c.execute('''INSERT INTO progress_record(task_id,reported_percent,report_text,reported_at)
                          VALUES(?,80,'较早的进展',?)''',(task,'2026-11-08T16:00:00+08:00'))
        self.c.commit()
        r=self.generate();item=r['snapshot']['tasks'][0]
        self.assertEqual(item['latest_progress'],'完成确认')
        self.assertEqual(item['completed_at'],'2026-11-08T17:30:00+08:00')
        self.assertEqual(r['snapshot']['metrics']['completed'],1)

    def test_acceptance_wait_uses_submission_audit_not_later_task_edit(self):
        task=self.task(work_status='awaiting_acceptance',updated_at='2026-11-08T17:00:00+08:00')
        self.c.execute('''INSERT INTO audit_log(object_type,object_id,action,actor,after_json,created_at)
                          VALUES('task',?,'update','local',?,?)''',
                       (task,json.dumps({'work_status':'awaiting_acceptance'}),'2026-11-05T10:00:00+08:00'))
        self.c.commit()
        r=self.generate()
        self.assertIn('待验收超过 48 小时',r['snapshot']['tasks'][0]['risk_reasons'])

    def test_history_filters_and_text_export_api_rejects_spoofed_identity(self):
        self.task();r=self.generate()
        status,data=self.api('/api/weekly-reports?year=2026&month=11&week=45')
        self.assertEqual(status,200);self.assertEqual(len(data['items']),1)
        self.assertTrue(data['pending'])
        status,data=self.api(f"/api/weekly-reports/{r['id']}/text")
        self.assertEqual(status,200);self.assertIn('科技产品中心部门工作总结报告',data['text'])
        self.assertIn('采购审批验证',data['text'])
        status,_=self.api(f'/api/weekly-reports?actor_id={self.manager}',actor=self.member)
        self.assertEqual(status,403)
        status,_=self.api(f"/api/weekly-reports/{r['id']}",'POST',{'actor_id':self.manager,'action':'publish','edit_revision':0},actor=self.pm)
        self.assertEqual(status,403)
        status,data=self.api(f"/api/weekly-reports/{r['id']}",'POST',{'edit_revision':0,'summary':'API 保存'},actor=self.pm)
        self.assertEqual(status,200);self.assertEqual(data['editorial']['summary'],'API 保存')
        status,_=self.api(f"/api/weekly-reports/{r['id']}",'POST',{'edit_revision':0,'summary':'冲突'})
        self.assertEqual(status,409)
        status,_=self.api('/api/weekly-reports?month=13');self.assertEqual(status,400)
        status,_=self.api('/api/weekly-reports/9999');self.assertEqual(status,404)

    def test_generate_api_manager_only_and_rejects_future_date(self):
        status,_=self.api('/api/weekly-reports/generate','POST',{'date':'2099-01-01'})
        self.assertEqual(status,400)
        status,_=self.api('/api/weekly-reports/generate','POST',{'date':'bad'})
        self.assertEqual(status,400)
        status,_=self.api('/api/weekly-reports/generate','POST',{},actor=self.pm)
        self.assertEqual(status,403)
        status,data=self.api('/api/weekly-reports/generate','POST',{})
        self.assertEqual(status,200);self.assertEqual(data['status'],'pending')

    def test_demo_preview_route_keeps_department_permissions(self):
        def request(user_id, submitted_id=None):
            handler=object.__new__(app.Handler)
            handler.command='GET';handler.path='/weekly-report-demo?actor_id='+str(submitted_id or user_id)
            handler.authenticated_user_id=user_id;handler.require_auth=lambda:True
            output=[]
            handler.json=lambda code,payload:output.append((code,payload))
            handler.send_bytes=lambda code,kind,data,cache:output.append((code,data))
            handler.do_GET()
            return output[0]
        with tempfile.TemporaryDirectory() as root:
            preview=Path(root)/'artifacts'/'weekly-report-demo'/'index.html'
            preview.parent.mkdir(parents=True);preview.write_text('<h1>虚构演示周报</h1>')
            with patch.object(app,'ROOT',Path(root)):
                for uid in (self.manager,self.pm):
                    code,payload=request(uid)
                    self.assertEqual(code,200);self.assertIn('虚构演示周报',payload.decode())
                self.assertEqual(request(self.member,self.manager)[0],403)
                preview.unlink()
                self.assertEqual(request(self.manager)[0],404)

    def test_schema_migration_is_repeatable_and_keeps_existing_report(self):
        r=self.generate()
        app.migrate(self.c);self.c.commit()
        self.assertEqual(reports.get_report(self.c,r['id'],self.manager)['snapshot'],r['snapshot'])

    def test_open_current_auto_creates_for_pm_once_without_overwriting_edits(self):
        code,first=self.api('/api/weekly-reports/current','POST',{},actor=self.pm)
        self.assertEqual(code,200);self.assertEqual(first['status'],'pending')
        self.assertEqual(first['source'],'on_open')
        saved=reports.update_report(self.c,first['id'],self.pm,{'edit_revision':0,'summary':'PM 提前整理的摘要'})
        self.c.commit()
        code,again=self.api('/api/weekly-reports/current','POST',{},actor=self.manager)
        self.assertEqual(code,200);self.assertEqual(again['id'],first['id'])
        self.assertEqual(again['editorial'],saved['editorial'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM weekly_report').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM weekly_report_job').fetchone()[0],0)
        self.assertEqual(self.api('/api/weekly-reports/current','POST',{},actor=self.member)[0],403)

    def test_refresh_preserves_editorial_order_and_records_changes_in_same_draft(self):
        first=self.task(title='第一项');second=self.task(title='第二项');removed=self.task(title='即将移除')
        report=self.generate()
        reports.update_report(self.c,report['id'],self.pm,{'edit_revision':0,'summary':'人工摘要',
            'conclusion':'人工管理结论','requirements':'下周要求','support':'资源协调',
            'notes':{str(first):'保留说明',str(removed):'旧任务说明'},'orders':{'next_week':[second,first,removed]}})
        self.c.commit()
        self.c.execute('DELETE FROM task WHERE id=?',(removed,))
        self.c.execute('UPDATE task SET title=? WHERE id=?',('第一项已更新',first));self.c.commit()
        added=self.task(title='新加入任务')
        real_clock=reports.local_time
        with patch.object(reports,'local_time',side_effect=lambda v=None:real_clock(v or self.generated)):
            changes=reports.report_changes(self.c,report['id'],self.pm)
            self.assertEqual([t['id'] for t in changes['added']],[added])
            self.assertEqual([t['id'] for t in changes['removed']],[removed])
            self.assertEqual([t['id'] for t in changes['changed']],[first])
            refreshed=reports.refresh_report(self.c,report['id'],self.pm,1);self.c.commit()
        self.assertEqual((refreshed['id'],refreshed['version']),(report['id'],report['version']))
        self.assertEqual(refreshed['edit_revision'],2)
        self.assertEqual(refreshed['editorial']['summary'],'人工摘要')
        self.assertEqual(refreshed['editorial']['conclusion'],'人工管理结论')
        self.assertEqual(refreshed['editorial']['requirements'],'下周要求')
        self.assertEqual(refreshed['editorial']['support'],'资源协调')
        self.assertEqual(refreshed['editorial']['notes'],{str(first):'保留说明'})
        self.assertEqual(refreshed['editorial']['orders']['next_week'],[second,first,added])
        old=json.loads(self.c.execute("SELECT before_json FROM audit_log WHERE action='refresh_data'").fetchone()[0])
        self.assertEqual(old['snapshot'],report['snapshot'])
        with self.assertRaises(reports.ReportConflict):reports.refresh_report(self.c,report['id'],self.pm,1)

    def test_refresh_updates_system_summary_but_preserves_explicit_empty_summary(self):
        self.task();report=self.generate();self.task(title='新任务')
        real_clock=reports.local_time
        with patch.object(reports,'local_time',side_effect=lambda v=None:real_clock(v or self.generated)):
            refreshed=reports.refresh_report(self.c,report['id'],self.pm,0);self.c.commit()
            self.assertEqual(refreshed['editorial']['summary'],refreshed['snapshot']['summary'])
            self.assertNotEqual(refreshed['editorial']['summary'],report['snapshot']['summary'])
            reports.update_report(self.c,report['id'],self.pm,{'edit_revision':1,'summary':''});self.c.commit()
            self.task(title='又一项任务')
            refreshed=reports.refresh_report(self.c,report['id'],self.pm,2);self.c.commit()
            self.assertEqual(refreshed['editorial']['summary'],'')

    def test_revision_copies_published_snapshot_and_is_idempotent(self):
        task=self.task();report=self.generate()
        published=reports.update_report(self.c,report['id'],self.manager,{'action':'publish','edit_revision':0,'summary':'发布摘要'})
        self.c.commit();self.c.execute('DELETE FROM task WHERE id=?',(task,));self.c.commit()
        draft=reports.revise_report(self.c,report['id'],self.manager);self.c.commit()
        self.assertEqual(draft['snapshot'],published['snapshot'])
        self.assertEqual(draft['editorial'],published['editorial'])
        self.assertEqual(draft['revision_of'],report['id'])
        self.assertEqual(reports.revise_report(self.c,report['id'],self.manager)['id'],draft['id']);self.c.commit()
        with self.assertRaises(PermissionError):reports.revise_report(self.c,report['id'],self.pm)
        with self.assertRaises(reports.ReportConflict):reports.refresh_report(self.c,report['id'],self.manager,1)
        reports.update_report(self.c,draft['id'],self.manager,{'action':'publish','edit_revision':0});self.c.commit()
        self.assertEqual(reports.get_report(self.c,report['id'],self.manager),published)
        with self.assertRaises(reports.ReportConflict):reports.revise_report(self.c,report['id'],self.manager)

    def test_current_refresh_revision_endpoints_and_permissions(self):
        code,report=self.api('/api/weekly-reports/current','POST',{},actor=self.pm)
        path=f"/api/weekly-reports/{report['id']}"
        self.assertEqual(self.api(path+'/changes',actor=self.pm)[0],200)
        self.assertEqual(self.api(path+'/changes',actor=self.member)[0],403)
        self.assertEqual(self.api(path+'/refresh','POST',{'edit_revision':0},actor=self.member)[0],403)
        code,refreshed=self.api(path+'/refresh','POST',{'edit_revision':0},actor=self.pm)
        self.assertEqual(code,200);self.assertEqual(refreshed['id'],report['id'])
        self.assertEqual(self.api(path+'/refresh','POST',{'edit_revision':0},actor=self.pm)[0],409)
        self.assertEqual(self.api(path,'POST',{'action':'publish','edit_revision':1},actor=self.manager)[0],200)
        self.assertEqual(self.api(path+'/revise','POST',{},actor=self.pm)[0],403)
        code,draft=self.api(path+'/revise','POST',{},actor=self.manager)
        self.assertEqual(code,200);self.assertEqual(draft['revision_of'],report['id'])
        self.assertEqual(self.api('/api/weekly-reports/current','POST',{})[1]['id'],draft['id'])

    def test_sunday_job_refreshes_early_draft_and_preserves_manual_edits(self):
        first=reports.generate_report(self.c,self.pm,'2026-11-02','2026-11-03T10:00:00+08:00',create_if_missing=True)
        self.c.commit()
        reports.update_report(self.c,first['id'],self.pm,{'edit_revision':0,'summary':'提前整理的内容','support':'需业务协调'});self.c.commit()
        task=self.task(title='周五新增任务',created_at='2026-11-06T10:00:00+08:00')
        result=reports.run_due(self.c,self.generated);self.c.commit()
        self.assertEqual(result['report_id'],first['id'])
        refreshed=reports.get_report(self.c,first['id'],self.pm)
        self.assertEqual(refreshed['editorial']['summary'],'提前整理的内容')
        self.assertEqual(refreshed['editorial']['support'],'需业务协调')
        self.assertIn(task,[t['id'] for t in refreshed['snapshot']['tasks']])
        self.assertEqual(reports.run_due(self.c,self.generated),{'generated':0})


if __name__ == '__main__':
    unittest.main()
