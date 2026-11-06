import copy
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app
import monthly_reports
import responsibility_domains as domains
import weekly_reports


class ResponsibilityDomainTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1','AI_API_KEY':'','FEISHU_APP_ID':'','WECOM_SECRET':''})
        env.start();self.addCleanup(env.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        database=patch.object(app,'DB_PATH',Path(temporary.name)/'domains.db')
        database.start();self.addCleanup(database.stop)
        app.initialize();self.c=app.db();self.addCleanup(self.c.close)
        self.manager=self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm=self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]

    def api(self,path,body=None,actor=None):
        handler=object.__new__(app.Handler);handler.path=path;handler.headers={}
        handler.command='GET' if body is None else 'POST'
        handler.authenticated_user_id=actor or self.manager;handler.require_auth=lambda:True
        handler.body=lambda:copy.deepcopy(body);out=[];handler.json=lambda status,payload:out.append((status,payload))
        (handler.do_GET if body is None else handler.do_POST)()
        return out[0]

    def create(self,name='自定义责任域',**changes):
        status,out=self.api('/api/domains',{'name':name,'boundary':'测试责任范围','owner_id':self.manager,**changes})
        self.assertEqual(status,201,out);return out['id']

    def task(self,domain,**changes):
        values={'title':'责任域历史任务','owner_id':self.member,'org_node_id':domain,'due_at':'2026-10-30T17:00:00+08:00',
                'priority':'P0','work_status':'in_progress','approval_status':'approved','deliverable':'交付记录',
                'estimated_hours':4,'actual_hours':3,'created_at':'2026-10-01T09:00:00+08:00','updated_at':'2026-10-10T09:00:00+08:00'}
        values.update(changes)
        tid=self.c.execute('INSERT INTO task('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
        self.c.commit();return tid

    def test_create_edit_delete_unused_domain_and_validation(self):
        domain=self.create(domain_code='CUSTOM',operating_line='自定义条线')
        status,result=self.api('/api/domains')
        self.assertEqual(status,200);row=next(row for row in result['items'] if row['id']==domain)
        self.assertTrue(row['can_delete']);self.assertEqual(row['owner_name'],'科技产品中心负责人')
        self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'修改后的责任域','boundary':'新的边界'})[0],200)
        for payload in ({'name':''},{'name':'x'*201},{'name':'修改后的责任域'},
                        {'name':'不合法编码','domain_code':'!bad'},{'name':'不存在负责人','owner_id':99999}):
            self.assertEqual(self.api('/api/domains',payload)[0],400,payload)
        self.assertEqual(self.api('/api/domains/'+str(domain),{'action':'delete'})[0],200)
        self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'不存在'})[0],404)

    def test_real_member_identity_cannot_be_overridden_by_submitted_manager(self):
        domain=self.create()
        for path,body in (('/api/domains',{'name':'越权创建','actor_id':self.manager}),
                          ('/api/domains/'+str(domain),{'name':'越权改名','actor_id':self.manager}),
                          ('/api/domains/'+str(domain),{'action':'delete','actor_id':self.manager})):
            self.assertEqual(self.api(path,body,self.member)[0],403)
        self.assertEqual(self.api('/api/domains?actor_id='+str(self.manager),actor=self.member)[0],403)
        self.assertEqual(self.api('/api/domains/'+str(domain),{'boundary':'PM可编辑'},self.pm)[0],200)

    def test_all_task_statuses_block_delete_but_allow_metadata_updates(self):
        for status in ('assigned','completed','cancelled','draft'):
            domain=self.create('状态责任域'+status);tid=self.task(domain,work_status=status)
            self.assertEqual(self.api('/api/domains/'+str(domain),{'action':'delete'})[0],409)
            self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'已修改'+status})[0],200)
            self.assertEqual(self.c.execute('SELECT org_node_id FROM task WHERE id=?',(tid,)).fetchone()[0],domain)

    def test_inactive_templates_and_retained_drafts_are_references(self):
        domain=self.create()
        self.c.execute("INSERT INTO task_template(org_node_id,title,owner_id,deliverable,recurrence,status,created_at) VALUES(?,?,?,?,'WEEKLY','inactive',?)",
                       (domain,'停用例行模板',self.member,'交付物',app.now()));self.c.commit()
        self.assertEqual(self.api('/api/domains/'+str(domain),{'action':'delete'})[0],409)
        draft_domain=self.create('草稿引用责任域')
        self.c.execute("INSERT INTO conversation_draft(session_id,raw_text,slots_json,status,expire_at,created_at,updated_at) VALUES(?,?,?,'discarded',?,?,?)",
                       ('test','材料',json.dumps({'org_node_id':draft_domain,'domain_name':'草稿引用责任域'}),'2099-01-01',app.now(),app.now()));self.c.commit()
        self.assertEqual(self.api('/api/domains/'+str(draft_domain),{'action':'delete'})[0],409)
        self.api('/api/domains/'+str(draft_domain),{'name':'草稿已同步'})
        self.assertEqual(json.loads(self.c.execute('SELECT slots_json FROM conversation_draft').fetchone()[0])['domain_name'],'草稿已同步')

    def test_child_nodes_and_department_roots_are_protected(self):
        parent=self.create('父责任域');child=self.create('子责任域',parent_id=parent)
        self.assertEqual(self.api('/api/domains/'+str(parent),{'action':'delete'})[0],409)
        self.assertEqual(self.api('/api/domains/'+str(child),{'action':'delete'})[0],200)
        self.assertEqual(self.api('/api/domains/'+str(parent),{'action':'delete'})[0],200)
        root=self.c.execute("SELECT id FROM org_node WHERE node_type='department' LIMIT 1").fetchone()[0]
        self.assertEqual(self.api('/api/domains/'+str(root),{'action':'delete'})[0],404)

    def test_deleted_task_audit_still_blocks_deletion(self):
        domain=self.create();tid=self.task(domain)
        self.assertEqual(self.api('/api/tasks/'+str(tid),{'action':'delete','operator_id':self.manager})[0],200)
        response=self.api('/api/domains/'+str(domain),{'action':'delete'})
        self.assertEqual(response[0],409);self.assertIn('历史引用记录',response[1]['error'])

    def test_report_only_reference_survives_deleted_source_task(self):
        domain=self.create();tid=self.task(domain)
        report=monthly_reports.generate(self.c,self.member,'2026-10');self.c.commit()
        self.c.execute('DELETE FROM task WHERE id=?',(tid,));self.c.commit()
        response=self.api('/api/domains/'+str(domain),{'action':'delete'})
        self.assertEqual(response[0],409);self.assertIn('历史月报',response[1]['error'])
        self.assertEqual(report['snapshot']['tasks'][0]['domain_id'],domain)

    def test_rename_synchronizes_published_and_archived_reports_without_rewriting_facts_or_manual_text(self):
        domain=self.create('历史责任域');tid=self.task(domain)
        old_task=dict(self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone())
        weekly=weekly_reports.generate_report(self.c,self.manager,'2026-10-12','2026-10-18T18:00:00+08:00');self.c.commit()
        weekly=weekly_reports.update_report(self.c,weekly['id'],self.manager,{'edit_revision':weekly['edit_revision'],'summary':'人工总结：历史责任域是原始表述','action':'publish'});self.c.commit()
        monthly=monthly_reports.generate(self.c,self.member,'2026-10');self.c.commit()
        outline=copy.deepcopy(monthly['editorial']['outline'])
        group=next(child for section in outline['children'] for child in section['children'] if child['kind']=='group')
        group['text']='手工分组标题'
        monthly=monthly_reports.update(self.c,monthly['id'],self.member,{'edit_revision':0,'outline':outline,'action':'archive'});self.c.commit()
        weekly_draft=weekly_reports.revise_report(self.c,weekly['id'],self.manager);self.c.commit()
        monthly_draft=monthly_reports.revise(self.c,monthly['id'],self.member);self.c.commit()
        status,result=self.api('/api/domains/'+str(domain),{'name':'更新后的责任域','boundary':'新边界','owner_id':self.pm})
        self.assertEqual(status,200,result);self.assertEqual(result['history_updated']['weekly_reports'],2)
        self.assertEqual(result['history_updated']['monthly_reports'],2)
        updated_weekly=weekly_reports.get_report(self.c,weekly['id'],self.manager)
        updated_monthly=monthly_reports.get_report(self.c,monthly['id'],self.member)
        for before,after in ((weekly,updated_weekly),(monthly,updated_monthly)):
            self.assertEqual(after['status'],before['status']);self.assertEqual(after['snapshot']['metrics'],before['snapshot']['metrics'])
            self.assertEqual(after['generated_at'],before['generated_at']);self.assertEqual(after['snapshot']['data_cutoff'],before['snapshot']['data_cutoff'])
            self.assertEqual(after['edit_revision'],before['edit_revision']+1)
            self.assertTrue(all(task['domain_name']=='更新后的责任域' for task in after['snapshot']['tasks']))
        self.assertEqual(updated_weekly['editorial'],weekly['editorial'])
        groups=[child for section in updated_monthly['editorial']['outline']['children'] for child in section['children'] if child['kind']=='group']
        self.assertTrue(any(group['text']=='更新后的责任域' for group in groups))
        self.assertTrue(any(group['text']=='手工分组标题' for group in groups))
        self.assertEqual(dict(self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()),old_task)
        self.assertIn('更新后的责任域',weekly_reports.meeting_text(updated_weekly))
        self.assertIn('更新后的责任域',monthly_reports.text_export(updated_monthly))
        with self.assertRaises(weekly_reports.ReportConflict):weekly_reports.update_report(self.c,weekly_draft['id'],self.manager,{'edit_revision':0,'summary':'过时内容'})
        self.c.rollback()
        with self.assertRaises(monthly_reports.Conflict):monthly_reports.update(self.c,monthly_draft['id'],self.member,{'edit_revision':0,'outline':monthly_draft['editorial']['outline']})
        self.c.rollback()

    def test_legacy_monthly_groups_without_domain_ids_are_renamed(self):
        domain=self.create('旧格式责任域');self.task(domain)
        monthly=monthly_reports.generate(self.c,self.member,'2026-10');self.c.commit()
        outline=monthly['editorial']['outline']
        for section in outline['children']:
            for child in section['children']:child.pop('domain_id',None)
        self.c.execute('UPDATE monthly_report SET outline_json=? WHERE id=?',(json.dumps(outline,ensure_ascii=False),monthly['id']));self.c.commit()
        self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'新格式责任域'})[0],200)
        result=monthly_reports.get_report(self.c,monthly['id'],self.member)
        self.assertIn('新格式责任域',monthly_reports.text_export(result))
        self.assertNotIn('旧格式责任域',monthly_reports.text_export(result))

    def test_startup_preserves_seed_domain_edits_and_deletions(self):
        row=self.c.execute("SELECT id FROM org_node WHERE domain_code='A1'").fetchone();self.assertIsNotNone(row)
        domain=row[0];self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'维护后的种子域','domain_code':'RENAMED','boundary':'自定义责任边界'})[0],200)
        another=self.c.execute("SELECT id FROM org_node WHERE domain_code='A2'").fetchone()[0]
        self.assertEqual(self.api('/api/domains/'+str(another),{'action':'delete'})[0],200)
        app.initialize()
        self.assertEqual(self.c.execute('SELECT name FROM org_node WHERE id=?',(domain,)).fetchone()[0],'维护后的种子域')
        self.assertFalse(self.c.execute('SELECT 1 FROM org_node WHERE id=?',(another,)).fetchone())

    def test_write_guards_prevent_dangling_references_after_delete(self):
        domain=self.create();self.assertEqual(self.api('/api/domains/'+str(domain),{'action':'delete'})[0],200)
        with self.assertRaises(sqlite3.IntegrityError):self.task(domain)
        self.c.rollback()
        kept=self.create('不能绕过引用检查');self.task(kept)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM org_node WHERE id=?',(kept,))
        self.c.rollback()

    def test_failed_history_update_rolls_back_definition_and_snapshots(self):
        domain=self.create('事务责任域');self.task(domain)
        monthly=monthly_reports.generate(self.c,self.member,'2026-10');self.c.commit()
        original=self.c.execute('SELECT snapshot_json FROM monthly_report WHERE id=?',(monthly['id'],)).fetchone()[0]
        with patch.object(domains,'sync_history',side_effect=ValueError('模拟同步失败')):
            self.assertEqual(self.api('/api/domains/'+str(domain),{'name':'不应保存'})[0],400)
        self.assertEqual(self.c.execute('SELECT name FROM org_node WHERE id=?',(domain,)).fetchone()[0],'事务责任域')
        self.assertEqual(self.c.execute('SELECT snapshot_json FROM monthly_report WHERE id=?',(monthly['id'],)).fetchone()[0],original)

    def test_concurrent_creation_cannot_duplicate_names(self):
        def create(_):
            with app.db() as c:
                try:result=domains.create(c,{'name':'并发自定义'},self.manager);c.commit();return result['id']
                except ValueError:c.rollback();return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,range(2)))
        self.assertEqual(sum(value is not None for value in results),1)


if __name__=='__main__':unittest.main()
