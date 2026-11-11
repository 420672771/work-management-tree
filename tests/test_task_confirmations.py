import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class TaskConfirmationTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'WMT_AUTH_MODE':'account','WMT_MONTHLY_DEMO':'0','AI_API_KEY':'','AI_API_BASE':'',
                                         'FEISHU_APP_ID':'','FEISHU_APP_SECRET':'','WECOM_CORP_ID':'','WECOM_SECRET':''})
        environment.start();self.addCleanup(environment.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        database=patch.object(app,'DB_PATH',Path(temporary.name)/'confirmations.db')
        database.start();self.addCleanup(database.stop)
        app.initialize();self.c=app.db();self.addCleanup(self.c.close)
        self.manager=self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm=self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]
        self.other=self.c.execute("INSERT INTO user(name,role) VALUES('其他PM','pm')").lastrowid
        self.domain=self.c.execute("SELECT id FROM org_node WHERE node_type='domain' LIMIT 1").fetchone()[0]
        for uid in (self.manager,self.pm,self.member,self.other):
            self.c.execute('INSERT INTO login_session VALUES(?,?,?,?,?)',('confirmation-'+str(uid),uid,
                           (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),app.now(),app.now()))
        self.c.commit()

    def api(self,path,actor,body=None):
        handler=object.__new__(app.Handler)
        handler.path=path;handler.command='POST' if body is not None else 'GET'
        handler.headers={'Cookie':'wmt_session=confirmation-'+str(actor),'Content-Type':'application/json'}
        handler.body=lambda:copy.deepcopy(body)
        result=[];handler.json=lambda status,payload:result.append((status,payload))
        (handler.do_POST if body is not None else handler.do_GET)()
        return result[0]

    def create(self,owner,title='确认规则测试'):
        status,out=self.api('/api/tasks',owner,{'title':title,'owner_id':owner,'org_node_id':self.domain,
                                              'due_at':'2030-11-20T17:00:00+08:00'})
        self.assertEqual(status,201);self.assertEqual(out['approval_status'],'pending')
        return out['id'],out['approval_id']

    def confirm(self,aid,actor,result='approved',**fields):
        return self.api(f'/api/approvals/{aid}/confirm',actor,{'result':result,**fields})

    def test_pm_can_be_confirmed_by_self_or_assigned_superior_and_records_operator(self):
        for actor in (self.pm,self.manager):
            tid,aid=self.create(self.pm,'PM确认'+str(actor))
            view=self.api('/api/approvals/'+str(aid),actor)[1]
            self.assertEqual(set(view['eligible_approver_ids']),{self.pm,self.manager})
            self.assertEqual(self.confirm(aid,actor)[0],200)
            task=self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
            self.assertEqual(task['approval_status'],'approved');self.assertEqual(task['confirmed_by'],actor)
            self.assertEqual(self.c.execute('SELECT operated_by FROM approval_record WHERE id=?',(aid,)).fetchone()[0],actor)

    def test_department_manager_requires_an_explicit_self_confirmation(self):
        tid,aid=self.create(self.manager)
        self.assertIsNotNone(aid)
        self.assertEqual(self.api('/api/approvals/'+str(aid),self.manager)[1]['eligible_approver_ids'],[self.manager])
        self.assertEqual(self.confirm(aid,self.pm)[0],403)
        self.assertEqual(self.confirm(aid,self.manager)[0],200)
        self.assertEqual(self.c.execute('SELECT confirmed_by FROM task WHERE id=?',(tid,)).fetchone()[0],self.manager)

    def test_ai_conversation_draft_creation_uses_the_same_managerial_confirmation_rule(self):
        for owner in (self.pm,self.manager):
            status,draft=self.api('/api/task/draft/parse',owner,{'text':'专项方案设计'+str(owner),'user_id':owner})
            self.assertEqual(status,200)
            did=draft['id']
            self.assertEqual(self.api(f'/api/task/draft/{did}/complete',owner,
                                     {'title':'专项方案设计'+str(owner),'owner_id':owner,'org_node_id':self.domain,'due_at':'2030-11-20T17:00:00+08:00'})[0],200)
            status,out=self.api(f'/api/task/draft/{did}/confirm',owner,{})
            self.assertEqual(status,200);self.assertEqual(out['status'],'pending');self.assertIsNotNone(out['approval_id'])
            self.assertEqual(self.confirm(out['approval_id'],owner)[0],200)

    def test_unrelated_pm_and_forged_operator_do_not_gain_confirmation_rights(self):
        _,aid=self.create(self.pm)
        for actor in (self.other,self.member):
            self.assertEqual(self.confirm(aid,actor,operator_id=self.manager)[0],403)
        self.assertEqual(self.c.execute('SELECT status FROM approval_record WHERE id=?',(aid,)).fetchone()[0],'pending')

    def test_regular_member_still_requires_assigned_approver(self):
        _,aid=self.create(self.member)
        self.assertEqual(self.confirm(aid,self.member)[0],403)
        self.assertEqual(self.confirm(aid,self.other)[0],403)
        self.assertEqual(self.confirm(aid,self.manager)[0],200)

    def test_pm_superior_can_confirm_even_when_an_active_delegate_is_assigned(self):
        self.c.execute("INSERT INTO delegate(user_id,delegate_user_id,scope,start_at,end_at) VALUES(?,?,'approval','2000-01-01','2099-01-01')",(self.manager,self.member));self.c.commit()
        tid,aid=self.create(self.pm)
        view=self.api('/api/approvals/'+str(aid),self.pm)[1]
        self.assertEqual(set(view['eligible_approver_ids']),{self.pm,self.manager,self.member})
        self.assertEqual(self.confirm(aid,self.manager)[0],200)

    def test_existing_pending_items_use_current_owner_role_without_rewriting_history(self):
        tid,aid=self.create(self.pm)
        before=dict(self.c.execute('SELECT * FROM approval_record WHERE id=?',(aid,)).fetchone())
        self.assertEqual(self.api('/api/approvals/'+str(aid),self.pm)[1]['eligible_approver_ids'],sorted([self.pm,self.manager]))
        self.assertEqual(dict(self.c.execute('SELECT * FROM approval_record WHERE id=?',(aid,)).fetchone()),before)
        self.c.execute("UPDATE user SET role='member' WHERE id=?",(self.pm,));self.c.commit()
        self.assertEqual(self.confirm(aid,self.pm)[0],403)
        self.assertEqual(self.confirm(aid,self.manager)[0],200)

    def test_department_manager_legacy_pending_item_ignores_old_foreign_assignment(self):
        tid,aid=self.create(self.manager)
        self.c.execute('UPDATE approval_record SET approver_id=? WHERE id=?',(self.pm,aid));self.c.commit()
        self.assertEqual(self.confirm(aid,self.pm)[0],403)
        self.assertEqual(self.confirm(aid,self.manager)[0],200)

    def test_rejection_and_duplicate_confirmation_do_not_overwrite_first_decision(self):
        tid,aid=self.create(self.pm)
        self.assertEqual(self.confirm(aid,self.pm,'rejected',reason='需要补充范围')[0],200)
        self.assertEqual(self.confirm(aid,self.manager)[0],404)
        task=self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        self.assertEqual(task['approval_status'],'rejected');self.assertEqual(task['confirmed_by'],self.pm)

    def test_concurrent_self_and_superior_only_one_decision_succeeds(self):
        tid,aid=self.create(self.pm)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(lambda actor:self.confirm(aid,actor),[self.pm,self.manager]))
        self.assertEqual(sorted(result[0] for result in results),[200,404])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM audit_log WHERE object_type='approval' AND object_id=? AND action='approved'",(aid,)).fetchone()[0],1)

    def test_owner_transfer_recomputes_self_confirmation_and_keeps_pending(self):
        tid,aid=self.create(self.pm)
        status,_=self.api('/api/tasks/'+str(tid),self.pm,{'owner_id':self.manager})
        self.assertEqual(status,200)
        view=self.api('/api/approvals/'+str(aid),self.manager)[1]
        self.assertEqual(view['status'],'pending');self.assertEqual(view['eligible_approver_ids'],[self.manager])
        self.assertEqual(self.confirm(aid,self.pm)[0],403);self.assertEqual(self.confirm(aid,self.manager)[0],200)

    def test_feishu_planning_execution_and_role_revocation_share_the_policy(self):
        tid,aid=self.create(self.pm)
        reply=app.process_bot_message(self.c,f'确认任务#{tid}通过',self.pm,'pm-self');self.c.commit()
        self.assertIn('回复“确认”',reply)
        self.c.execute("UPDATE user SET role='member' WHERE id=?",(self.pm,));self.c.commit()
        reply=app.process_bot_message(self.c,'确认',self.pm,'pm-self');self.c.commit()
        self.assertIn('未执行',reply)
        self.c.execute("UPDATE user SET role='pm' WHERE id=?",(self.pm,));self.c.commit()
        app.process_bot_message(self.c,f'确认任务#{tid}通过',self.pm,'pm-self');self.c.commit()
        self.assertIn('确认已通过',app.process_bot_message(self.c,'确认',self.pm,'pm-self'));self.c.commit()
        self.assertEqual(self.c.execute('SELECT confirm_source FROM task WHERE id=?',(tid,)).fetchone()[0],'feishu')

    def test_progress_and_acceptance_do_not_silently_gain_new_self_permissions(self):
        tid,aid=self.create(self.pm);self.confirm(aid,self.pm)
        status,progress=self.api(f'/api/tasks/{tid}/progress',self.pm,{'percent':50,'comment':'已完成方案初稿'})
        self.assertEqual(status,200);self.assertEqual(self.confirm(progress['approval_id'],self.pm)[0],403)
        self.assertEqual(self.confirm(progress['approval_id'],self.manager)[0],200)
        self.c.execute("UPDATE task SET work_status='awaiting_acceptance' WHERE id=?",(tid,));self.c.commit()
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.pm,{'result':'passed'})[0],403)
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.manager,{'result':'passed'})[0],200)

    def test_confirmation_reminders_reach_both_pm_candidates_and_manager_self(self):
        tid,aid=self.create(self.pm);manager_task,_=self.create(self.manager)
        with patch.object(app,'send_feishu_once',return_value='skipped_no_binding') as send:
            app.run_daily_jobs(self.c,kind='approval')
        recipients={(call.args[1],call.args[2]) for call in send.call_args_list}
        self.assertEqual(recipients,{(tid,self.pm),(tid,self.manager),(manager_task,self.manager)})


if __name__=='__main__':unittest.main()
