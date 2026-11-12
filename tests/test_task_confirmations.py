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
            # An unrelated employee cannot discover the approval by its ID.
            self.assertEqual(self.confirm(aid,actor,operator_id=self.manager)[0],403 if actor==self.other else 404)
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

    def test_progress_and_acceptance_support_self_and_pm_superior_without_auto_approval(self):
        for owner,actor in ((self.pm,self.pm),(self.pm,self.manager),(self.manager,self.manager)):
            tid,aid=self.create(owner,f'本人工作全流程{owner}-{actor}');self.confirm(aid,owner)
            status,progress=self.api(f'/api/tasks/{tid}/progress',owner,{'percent':50,'comment':'已完成方案初稿'})
            self.assertEqual(status,200);self.assertIsNotNone(progress['approval_id'])
            self.assertEqual(self.c.execute('SELECT effective_percent FROM task WHERE id=?',(tid,)).fetchone()[0],0)
            view=self.api('/api/approvals/'+str(progress['approval_id']),owner)[1]
            self.assertIn(owner,view['eligible_approver_ids'])
            self.assertEqual(self.confirm(progress['approval_id'],self.other)[0],403)
            self.assertEqual(self.confirm(progress['approval_id'],actor)[0],200)
            self.assertEqual(self.c.execute('SELECT confirmed_by FROM progress_record WHERE id=?',(progress['id'],)).fetchone()[0],actor)
            self.assertEqual(self.c.execute('SELECT effective_percent FROM task WHERE id=?',(tid,)).fetchone()[0],50)
            self.assertEqual(self.api(f'/api/tasks/{tid}',owner,{'work_status':'awaiting_acceptance'})[0],200)
            detail=self.api(f'/api/tasks/{tid}',owner)[1];self.assertIn(owner,detail['eligible_acceptor_ids'])
            self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.other,{'result':'passed','acceptor_id':self.manager})[0],403)
            self.assertEqual(self.api(f'/api/tasks/{tid}/accept',actor,{'result':'passed'})[0],200)
            self.assertEqual(self.c.execute('SELECT acceptor_id FROM acceptance WHERE task_id=?',(tid,)).fetchone()[0],actor)

    def test_regular_member_progress_and_acceptance_still_require_assigned_superior(self):
        tid,aid=self.create(self.member);self.confirm(aid,self.manager)
        status,progress=self.api(f'/api/tasks/{tid}/progress',self.member,{'percent':50,'comment':'完成初稿'})
        self.assertEqual(status,200);self.assertEqual(self.confirm(progress['approval_id'],self.member)[0],403)
        self.assertEqual(self.confirm(progress['approval_id'],self.manager)[0],200)
        self.api(f'/api/tasks/{tid}',self.member,{'work_status':'awaiting_acceptance'})
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.member,{'result':'passed'})[0],403)
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.manager,{'result':'passed'})[0],200)

    def test_managerial_routine_100_percent_progress_still_waits_for_self_acceptance(self):
        tid,aid=self.create(self.manager);self.confirm(aid,self.manager)
        self.c.execute("UPDATE task SET task_type='ROUTINE',routine_week_start='2030-11-18' WHERE id=?",(tid,));self.c.commit()
        _,progress=self.api(f'/api/tasks/{tid}/progress',self.manager,{'percent':100,'comment':'本周例行交付完成'})
        self.assertEqual(self.confirm(progress['approval_id'],self.manager)[0],200)
        self.assertEqual(self.c.execute('SELECT work_status FROM task WHERE id=?',(tid,)).fetchone()[0],'awaiting_acceptance')
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.manager,{'result':'passed'})[0],200)

    def test_existing_progress_permission_is_live_and_head_does_not_need_old_foreign_approver(self):
        tid,aid=self.create(self.manager);self.confirm(aid,self.manager)
        _,progress=self.api(f'/api/tasks/{tid}/progress',self.manager,{'percent':30,'comment':'旧确认人回归'})
        self.c.execute('UPDATE approval_record SET approver_id=? WHERE id=?',(self.pm,progress['approval_id']));self.c.commit()
        self.assertEqual(self.confirm(progress['approval_id'],self.pm)[0],403)
        self.assertEqual(self.confirm(progress['approval_id'],self.manager)[0],200)

    def test_repair_of_missing_managerial_progress_approval_is_idempotent_and_preserves_history(self):
        tid,aid=self.create(self.manager);self.confirm(aid,self.manager)
        stamp=app.now()
        rid=self.c.execute("INSERT INTO progress_record(task_id,reported_percent,report_text,status,reported_at) VALUES(?,40,'旧进度缺少确认人','pending',?)",(tid,stamp)).lastrowid
        rejected=self.c.execute("INSERT INTO progress_record(task_id,reported_percent,report_text,status,reported_at) VALUES(?,20,'已驳回记录','pending',?)",(tid,stamp)).lastrowid
        rejected_approval=app.create_approval(self.c,'progress_confirm',rejected,tid,self.manager)
        self.c.execute("UPDATE approval_record SET status='rejected' WHERE id=?",(rejected_approval,));self.c.commit()
        app.initialize();app.initialize()
        repaired=self.c.execute("SELECT * FROM approval_record WHERE biz_type='progress_confirm' AND biz_id=?",(rid,)).fetchall()
        self.assertEqual(len(repaired),1);self.assertEqual(repaired[0]['approver_id'],self.manager)
        self.assertEqual(self.c.execute('SELECT status FROM approval_record WHERE id=?',(rejected_approval,)).fetchone()[0],'rejected')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM approval_record WHERE biz_id=? AND biz_type=?',(rejected,'progress_confirm')).fetchone()[0],1)
        self.assertEqual(self.confirm(repaired[0]['id'],self.manager)[0],200)

    def test_concurrent_pm_self_and_superior_acceptance_only_records_one_decision(self):
        tid,aid=self.create(self.pm);self.confirm(aid,self.pm)
        self.c.execute("UPDATE task SET work_status='awaiting_acceptance' WHERE id=?",(tid,));self.c.commit()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(lambda actor:self.api(f'/api/tasks/{tid}/accept',actor,{'result':'passed'}),[self.pm,self.manager]))
        self.assertEqual(sorted(result[0] for result in results),[200,400])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM acceptance WHERE task_id=?',(tid,)).fetchone()[0],1)

    def test_self_acceptance_can_reject_then_resubmit_with_real_operators_recorded(self):
        tid,aid=self.create(self.pm);self.confirm(aid,self.pm)
        self.c.execute("UPDATE task SET work_status='awaiting_acceptance' WHERE id=?",(tid,));self.c.commit()
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.pm,{'result':'rejected','reason':'补充交付物'})[0],200)
        self.assertEqual(self.c.execute('SELECT work_status FROM task WHERE id=?',(tid,)).fetchone()[0],'in_progress')
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.manager,{'result':'passed'})[0],400)
        self.api(f'/api/tasks/{tid}',self.pm,{'work_status':'awaiting_acceptance'})
        self.assertEqual(self.api(f'/api/tasks/{tid}/accept',self.manager,{'result':'passed'})[0],200)
        self.assertEqual([row[0] for row in self.c.execute('SELECT acceptor_id FROM acceptance WHERE task_id=? ORDER BY id',(tid,))],[self.pm,self.manager])

    def test_feishu_self_progress_and_acceptance_recheck_role_when_confirming(self):
        tid,aid=self.create(self.pm);self.confirm(aid,self.pm)
        _,progress=self.api(f'/api/tasks/{tid}/progress',self.pm,{'percent':60,'comment':'飞书确认进度'})
        reply=app.process_bot_message(self.c,f'确认进度任务#{tid}通过',self.pm,'self-progress');self.c.commit()
        self.assertIn('回复“确认”',reply)
        self.assertIn('确认已通过',app.process_bot_message(self.c,'确认',self.pm,'self-progress'));self.c.commit()
        self.c.execute("UPDATE task SET work_status='awaiting_acceptance' WHERE id=?",(tid,));self.c.commit()
        self.assertIn('回复“确认”',app.process_bot_message(self.c,f'验收任务#{tid}通过',self.pm,'self-accept'));self.c.commit()
        self.c.execute("UPDATE user SET role='member' WHERE id=?",(self.pm,));self.c.commit()
        self.assertIn('未执行',app.process_bot_message(self.c,'确认',self.pm,'self-accept'));self.c.commit()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM acceptance WHERE task_id=?',(tid,)).fetchone()[0],0)
        self.c.execute("UPDATE user SET role='pm' WHERE id=?",(self.pm,));self.c.commit()
        app.process_bot_message(self.c,f'验收任务#{tid}通过',self.pm,'self-accept');self.c.commit()
        self.assertIn('验收通过',app.process_bot_message(self.c,'确认',self.pm,'self-accept'));self.c.commit()

    def test_confirmation_reminders_reach_both_pm_candidates_and_manager_self(self):
        tid,aid=self.create(self.pm);manager_task,_=self.create(self.manager)
        with patch.object(app,'send_feishu_once',return_value='skipped_no_binding') as send:
            app.run_daily_jobs(self.c,kind='approval')
        recipients={(call.args[1],call.args[2]) for call in send.call_args_list}
        self.assertEqual(recipients,{(tid,self.pm),(tid,self.manager),(manager_task,self.manager)})


if __name__=='__main__':unittest.main()
