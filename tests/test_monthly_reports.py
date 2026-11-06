import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app
import monthly_reports as reports


class MonthlyReportTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'0'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(app, 'DB_PATH', Path(self.temp.name) / 'monthly.db')
        self.db_patch.start()
        app.initialize()
        self.c = app.db()
        self.members = [r[0] for r in self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 2")]
        self.member, self.other = self.members
        self.manager = self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm = self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.domain = self.c.execute("SELECT id FROM org_node WHERE node_type='domain'").fetchone()[0]

    def tearDown(self):
        self.c.close()
        self.db_patch.stop()
        self.temp.cleanup()

    def task(self, **changes):
        fields = dict(title='月报测试任务', owner_id=self.member, org_node_id=self.domain,
                      due_at='2026-10-26T17:00:00+08:00', priority='P1', work_status='in_progress',
                      deliverable='验收记录', approval_status='approved', estimated_hours=8, actual_hours=10,
                      created_at='2026-10-02T10:00:00+08:00', updated_at='2026-10-20T10:00:00+08:00')
        fields.update(changes)
        result = self.c.execute('INSERT INTO task ('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', tuple(fields.values())).lastrowid
        self.c.commit()
        return result

    def generate(self, user=None, month='2026-10'):
        result = reports.generate(self.c, user or self.member, month)
        self.c.commit()
        return result

    def api(self, path, user=None, method='GET', body=None, session=True):
        handler = object.__new__(app.Handler)
        handler.path, handler.command, handler.headers = path, method, {}
        handler.require_auth = lambda: True
        handler.body = lambda: copy.deepcopy(body or {})
        # Use actual cookie parsing and database-backed sessions, even in demo mode.
        handler.authenticated_user_id = self.manager
        if session:
            token = 'monthly-test-' + str(user or self.member)
            self.c.execute('INSERT OR REPLACE INTO login_session VALUES(?,?,?,?,?)',
                (token, user or self.member, (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(), app.now(), app.now()))
            self.c.commit()
            handler.headers = {'Cookie':'wmt_session='+token}
        output = []
        handler.json = lambda status, payload: output.append((status, payload))
        (handler.do_GET if method == 'GET' else handler.do_POST)()
        return output[0]

    def test_source_is_personal_formal_and_china_month_scoped(self):
        p0 = self.task(priority='P0')
        done = self.task(priority='P2', work_status='completed')
        boundary = self.task(created_at='2026-09-30T16:00:00+00:00')
        old = self.task(created_at='2026-09-30T15:59:59+00:00', work_status='completed', updated_at='2026-09-30T15:59:59+00:00')
        for fields in ({'owner_id':self.other}, {'approval_status':'pending'}, {'work_status':'draft'},
                       {'work_status':'cancelled'}, {'created_at':'2026-10-31T16:00:00+00:00'}):
            self.task(**fields)
        self.task(priority='P2', due_at='2026-11-15T17:00:00+08:00')
        report = self.generate(); data = report['snapshot']
        self.assertEqual(len(data['tasks']), 5)
        self.assertTrue(all(t['owner_id']==self.member for t in data['tasks']))
        self.assertEqual(set(data['sections']['achievements']), {p0, done})
        self.assertIn(boundary, data['metric_tasks']['new'])
        self.assertNotIn(old, data['metric_tasks']['new'])
        self.assertEqual(data['metrics']['completed'], 1)
        self.assertEqual(data['metrics']['completed_hours'], {'standard':8.0,'actual':10.0})
        self.assertEqual(len(data['sections']['next_month']), 3)
        self.assertEqual(data['period_end'], '2026-10-31')
        self.assertNotIn('本周', json.dumps(report['editorial'], ensure_ascii=False))

    def test_generate_is_idempotent_and_archive_revise_preserves_snapshot(self):
        task_id = self.task()
        original = self.generate()
        self.assertEqual(self.generate()['id'], original['id'])
        outline = copy.deepcopy(original['editorial']['outline'])
        outline['children'][-1]['children'][0]['text'] = '本月完成接口梳理'
        saved = reports.update(self.c, original['id'], self.member, {'edit_revision':0,'outline':outline})
        self.c.commit()
        with self.assertRaises(reports.Conflict):
            reports.update(self.c, original['id'], self.member, {'edit_revision':0,'outline':outline})
        self.c.rollback()
        archived = reports.update(self.c, original['id'], self.member, {'action':'archive','edit_revision':1})
        self.c.commit()
        self.assertFalse(archived['can_edit'])
        with self.assertRaises(reports.Conflict):
            reports.update(self.c, original['id'], self.member, {'edit_revision':2})
        self.c.rollback()
        self.c.execute('DELETE FROM task WHERE id=?',(task_id,)); self.c.commit()
        revised = reports.revise(self.c, original['id'], self.member); self.c.commit()
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['revision_of'], original['id'])
        self.assertEqual(revised['snapshot'], original['snapshot'])
        self.assertEqual(revised['editorial'], saved['editorial'])
        self.assertEqual(reports.revise(self.c, original['id'], self.member)['id'], revised['id'])
        self.c.commit()
        self.assertEqual(reports.get_report(self.c, original['id'], self.member)['status'], 'archived')
        self.assertIn('本月完成接口梳理', reports.text_export(revised))

    def test_owner_list_and_management_readonly(self):
        own = self.generate(); other = self.generate(self.other)
        self.assertEqual([r['id'] for r in reports.list_reports(self.c,self.member)['items']], [own['id']])
        self.assertEqual(len(reports.list_reports(self.c,self.member)['people']), 1)
        with self.assertRaises(LookupError): reports.get_report(self.c,other['id'],self.member)
        for role in (self.manager,self.pm):
            self.assertEqual(len(reports.list_reports(self.c,role,scope='all')['items']), 2)
            self.assertFalse(reports.get_report(self.c,own['id'],role)['can_edit'])
            with self.assertRaises(PermissionError): reports.update(self.c,own['id'],role,{'edit_revision':0})
            self.c.rollback()
            with self.assertRaises(PermissionError): reports.revise(self.c,own['id'],role)
            self.c.rollback()
        for kwargs in ({'scope':'all'}, {'owner_id':self.other}):
            with self.assertRaises(PermissionError): reports.list_reports(self.c,self.member,**kwargs)

    def test_outline_keeps_statistics_and_rejects_foreign_task_references(self):
        self.task(); foreign = self.task(owner_id=self.other)
        report = self.generate(); base = report['editorial']['outline']
        variants = []
        value = copy.deepcopy(base); value['children'][0]['children'][0]['text']='新增任务：999 项'; variants.append(value)
        value = copy.deepcopy(base); value['children'][-1]['children'].append({'id':'foreign','kind':'task','text':'窃取','children':[],'task_id':foreign}); variants.append(value)
        value = copy.deepcopy(base); value['children'][-1]['children'][0]['html']='<script>'; variants.append(value)
        value = copy.deepcopy(base); value['children'][-1]['children'][0]['text']='x'*4001; variants.append(value)
        value = copy.deepcopy(base); value['children'][-1]['id']=value['children'][0]['id']; variants.append(value)
        for outline in variants:
            with self.subTest(outline=str(outline)[:80]), self.assertRaises(ValueError):
                reports.validate_outline(outline, report['snapshot'])
        with self.assertRaises(ValueError): reports.update(self.c,report['id'],self.member,{'edit_revision':0,'snapshot':{}})

    def test_all_generated_nodes_allow_children_deletion_and_sibling_order_saved_via_http(self):
        self.task()
        report = self.generate()
        original_snapshot = copy.deepcopy(report['snapshot'])
        outline = copy.deepcopy(report['editorial']['outline'])
        overview = outline['children'][0]
        overview['children'][0]['children'].append(reports.node('custom:metric-note','统计补充说明','custom'))
        overview['children'][0], overview['children'][1] = overview['children'][1], overview['children'][0]
        del overview['children'][2]
        outline['children'].reverse()
        del outline['children'][1]
        outline['children'].append(reports.node('custom:title-note','标题补充说明','custom'))
        status, saved = self.api('/api/monthly-reports/'+str(report['id']), method='POST',
                                body={'edit_revision':0,'outline':outline})
        self.assertEqual(status, 200)
        self.assertEqual(saved['editorial']['outline'], outline)
        self.assertEqual(saved['snapshot'], original_snapshot)
        status, loaded = self.api('/api/monthly-reports/'+str(report['id']))
        self.assertEqual(status, 200)
        self.assertEqual(loaded['editorial'], saved['editorial'])

    def test_delete_title_clear_rebuild_archive_and_revise_preserve_custom_structure(self):
        report = self.generate()
        path = '/api/monthly-reports/'+str(report['id'])
        empty = reports.node('monthly:outline','','container')
        status, saved = self.api(path, method='POST', body={'edit_revision':0,'outline':empty})
        self.assertEqual(status, 200)
        self.assertEqual(saved['editorial']['outline'], empty)
        self.assertEqual(saved['snapshot'], report['snapshot'])
        status, archived = self.api(path, method='POST', body={'edit_revision':1,'action':'archive'})
        self.assertEqual(status, 200)
        self.assertFalse(archived['can_edit'])
        status, revised = self.api(path+'/revise', method='POST')
        self.assertEqual(status, 200)
        rebuilt = reports.node('monthly:outline','','container',[
            reports.node('custom:plan','下月安排','custom',[
                reports.node('custom:child','新增工作','custom')]),
            reports.node('custom:summary','总结','custom')])
        status, saved = self.api('/api/monthly-reports/'+str(revised['id']), method='POST',
                                body={'edit_revision':0,'outline':rebuilt})
        self.assertEqual(status, 200)
        self.assertEqual(saved['editorial']['outline'], rebuilt)
        self.assertEqual(saved['snapshot'], report['snapshot'])
        self.assertIn('- 下月安排\n  - 新增工作\n- 总结', reports.text_export(saved))
        self.assertNotIn('\n- \n', reports.text_export(saved))

    def test_container_accepts_legacy_title_and_rejects_nested_or_malformed_containers(self):
        report = self.generate()
        legacy = report['editorial']['outline']
        container = reports.node('monthly:outline','','container',[legacy])
        reports.validate_outline(container,report['snapshot'])
        invalid = [reports.node('monthly:outline','','container',[copy.deepcopy(container)]),
                   reports.node('bad','','container'),
                   reports.node('monthly:outline','visible','container')]
        for outline in invalid:
            with self.subTest(outline=outline), self.assertRaises(ValueError):
                reports.validate_outline(outline,report['snapshot'])

    def test_month_validation_leap_and_empty_report(self):
        self.assertEqual(reports.period('2024-02')[2].isoformat(),'2024-02-29')
        for month in ('2026-13','2026-1','bad',[], '9999-12'):
            with self.subTest(month=month), self.assertRaises(ValueError): self.generate(month=month)
        empty = self.generate(month='2026-09')
        self.assertEqual(empty['snapshot']['metrics']['new'],0)
        self.assertEqual(len(empty['editorial']['outline']['children']),6)

    def test_http_identity_spoofing_detail_and_export_isolation(self):
        own = self.generate(); other = self.generate(self.other)
        for suffix in ('','/text','/export'):
            self.assertEqual(self.api('/api/monthly-reports/'+str(other['id'])+suffix)[0],404)
            self.assertEqual(self.api('/api/monthly-reports/'+str(other['id'])+suffix,user=self.pm)[0],200)
        self.assertEqual(self.api('/api/monthly-reports?scope=all&actor_id='+str(self.manager))[0],403)
        self.assertEqual(self.api('/api/monthly-reports?scope=all',session=False)[0],401)
        code, generated = self.api('/api/monthly-reports/generate',method='POST',body={'month':'2026-10','actor_id':self.other})
        self.assertEqual(code,200); self.assertEqual(generated['id'],own['id'])
        self.assertEqual(self.api('/api/monthly-reports/generate',method='POST',body={'month':'2026-10','user_id':self.other})[0],400)
        self.assertEqual(self.api('/api/monthly-reports/'+str(own['id']),method='POST',user=self.pm,body={'edit_revision':0,'action':'archive'})[0],403)
        self.assertEqual(self.api('/api/monthly-reports/'+str(own['id']),method='POST',body={'edit_revision':77,'action':'archive'})[0],409)
        self.assertEqual(self.api('/api/monthly-reports/generate',method='POST',session=False,body={'actor_id':self.manager})[0],401)

    def test_demo_switching_supports_generation_edit_archive_revise_and_exports(self):
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1'}):
            code,own=self.api('/api/monthly-reports/generate',method='POST',session=False,body={'month':'2026-10','actor_id':self.member})
            self.assertEqual(code,200);self.assertEqual(own['user_id'],self.member)
            code,other=self.api('/api/monthly-reports/generate',method='POST',session=False,body={'month':'2026-10','actor_id':self.other})
            self.assertEqual(code,200);self.assertEqual(other['user_id'],self.other)
            code,listing=self.api('/api/monthly-reports?actor_id='+str(self.member),session=False)
            self.assertEqual(code,200);self.assertEqual([r['id'] for r in listing['items']],[own['id']])
            for actor,report in ((self.member,own),(self.other,other)):
                outline=copy.deepcopy(report['editorial']['outline'])
                outline['children'][-1]['children'][0]['text']='演示员工 '+str(actor)+' 的总结'
                base='/api/monthly-reports/'+str(report['id'])
                self.assertEqual(self.api(base,method='POST',session=False,body={'actor_id':actor,'edit_revision':0,'outline':outline})[0],200)
                for suffix in ('/text','/export'):
                    self.assertEqual(self.api(base+suffix+'?actor_id='+str(actor),session=False)[0],200)
                code,archived=self.api(base,method='POST',session=False,body={'actor_id':actor,'action':'archive','edit_revision':1})
                self.assertEqual(code,200);self.assertEqual(archived['status'],'archived')
                code,revised=self.api(base+'/revise',method='POST',session=False,body={'actor_id':actor})
                self.assertEqual(code,200);self.assertEqual(revised['version'],2)
                self.assertEqual(revised['editorial']['outline'],outline)
            self.assertEqual(len(self.api('/api/monthly-reports?scope=all&actor_id='+str(self.pm),session=False)[1]['items']),4)

    def test_demo_gate_is_off_by_default_disabled_for_feishu_and_cannot_override_session(self):
        self.assertFalse(app.monthly_demo_enabled())
        self.assertEqual(self.api('/api/monthly-reports?actor_id='+str(self.member),session=False)[0],401)
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'feishu','WMT_MONTHLY_DEMO':'1'}):
            self.assertFalse(app.monthly_demo_enabled())
            self.assertEqual(self.api('/api/monthly-reports?actor_id='+str(self.member),session=False)[0],401)
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1'}):
            self.assertEqual(self.api('/api/monthly-reports',session=False)[0],400)
            self.assertEqual(self.api('/api/monthly-reports?actor_id=bad',session=False)[0],400)
            self.assertEqual(self.api('/api/monthly-reports?actor_id=9999999',session=False)[0],403)
            code,report=self.api('/api/monthly-reports/generate',method='POST',user=self.member,body={'actor_id':self.other,'month':'2026-10'})
            self.assertEqual(code,200);self.assertEqual(report['user_id'],self.member)
        self.assertEqual(self.api('/api/monthly-reports?actor_id='+str(self.member),session=False)[0],401)

    def test_session_payload_reports_demo_capability_without_claiming_login(self):
        handler=object.__new__(app.Handler);handler.headers={}
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1'}):
            state=handler.session_payload()
            self.assertTrue(state['monthly_demo']);self.assertFalse(state['authenticated']);self.assertIsNone(state['user'])
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'feishu','WMT_MONTHLY_DEMO':'1'}):
            self.assertFalse(handler.session_payload()['monthly_demo'])
