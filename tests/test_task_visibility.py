import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class TaskVisibilityTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'WMT_AUTH_MODE':'account','WMT_MONTHLY_DEMO':'0','AI_API_BASE':'','AI_API_KEY':'',
                                         'FEISHU_APP_ID':'','FEISHU_APP_SECRET':'','WECOM_CORP_ID':'','WECOM_SECRET':''})
        environment.start();self.addCleanup(environment.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        database=patch.object(app,'DB_PATH',Path(temporary.name)/'visibility.db')
        database.start();self.addCleanup(database.stop)
        app.initialize();self.c=app.db();self.addCleanup(self.c.close)
        self.manager=self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm=self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]
        self.leader=self.person('团队负责人')
        self.peer=self.person('其他团队成员')
        self.subleader=self.person('下级组长')
        self.junior=self.person('下级组员')
        self.empty=self.person('无待办成员')
        self.c.execute('UPDATE reporting_line SET direct_leader_id=? WHERE user_id=?',(self.leader,self.member))
        self.c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,effective_from) VALUES(?,?,?)',(self.subleader,self.leader,app.now()))
        self.c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,effective_from) VALUES(?,?,?)',(self.junior,self.subleader,app.now()))
        for uid in (self.manager,self.pm,self.member,self.leader,self.peer,self.junior,self.empty):
            self.c.execute('INSERT INTO login_session VALUES(?,?,?,?,?)',('visibility-'+str(uid),uid,
                           (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),app.now(),app.now()))
        self.own=self.task('本人正式任务',self.member)
        self.leader_task=self.task('组长正式任务',self.leader)
        self.junior_task=self.task('下级正式任务',self.junior)
        self.foreign=self.task('其他团队私有任务',self.peer)
        self.own_pending,self.own_approval=self.pending('本人待确认任务',self.member)
        self.pm_pending,self.pm_approval=self.pending('管理PM私有待确认任务',self.pm)
        self.head_pending,self.head_approval=self.pending('部门负责人私有待确认任务',self.manager)
        self.peer_pending,self.peer_approval=self.pending('其他团队待确认任务',self.peer)
        self.own_draft=self.draft('本人草稿',self.member)
        self.pm_draft=self.draft('管理PM私有草稿',self.pm)
        self.peer_draft=self.draft('其他团队私有草稿',self.peer)
        for uid in (self.member,self.peer,self.pm):
            self.c.execute("INSERT INTO daily_report_status(report_date,user_id,status,task_count,created_at) VALUES(?,?,'unreported',1,?)",(datetime.now(timezone.utc).date().isoformat(),uid,app.now()))
        self.c.commit()

    def person(self,name):
        return self.c.execute("INSERT INTO user(name,role,department) VALUES(?,'member','科技产品中心')",(name,)).lastrowid

    def task(self,title,owner,**changes):
        fields={'title':title,'owner_id':owner,'due_at':'2030-11-20T17:00:00+08:00','approval_status':'approved',
                'work_status':'in_progress','approver_id':self.manager,'created_at':app.now(),'updated_at':app.now()}
        fields.update(changes)
        return self.c.execute('INSERT INTO task('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid

    def pending(self,title,owner):
        tid=self.task(title,owner,approval_status='pending')
        aid=app.create_approval(self.c,'task_confirm',tid,tid,app.task_confirmation_approver(self.c,owner,self.manager))
        return tid,aid

    def draft(self,title,owner):
        stamp=app.now();slots={'title':title,'owner_id':owner,'due_at':'2030-11-20T17:00:00+08:00'}
        return self.c.execute("INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,'text',?,?,'[]','preview',?,?,?)",
                              ('draft-'+str(owner),owner,title,json.dumps(slots),
                               (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),stamp,stamp)).lastrowid

    def api(self,path,actor,body=None):
        self.c.commit()
        handler=object.__new__(app.Handler)
        handler.path=path;handler.command='POST' if body is not None else 'GET'
        handler.headers={'Cookie':'wmt_session=visibility-'+str(actor),'Content-Type':'application/json'}
        handler.body=lambda:copy.deepcopy(body)
        result=[];handler.json=lambda status,payload:result.append((status,payload))
        (handler.do_POST if body is not None else handler.do_GET)()
        return result[0]

    def ids(self,path,actor):
        status,items=self.api(path,actor);self.assertEqual(status,200)
        return {item['id'] for item in items}

    def test_member_lists_only_personal_tasks_approvals_and_drafts(self):
        self.assertEqual(self.ids('/api/tasks',self.member),{self.own})
        self.assertEqual(self.ids('/api/approvals',self.member),{self.own_approval})
        self.assertEqual(self.ids('/api/task/drafts',self.member),{self.own_draft})

    def test_team_scope_follows_actual_subtree_and_never_includes_upstream_pm_or_head(self):
        self.assertEqual(self.ids('/api/tasks',self.leader),{self.leader_task,self.own,self.junior_task})
        self.assertEqual(self.ids('/api/approvals',self.leader),{self.own_approval})
        self.assertEqual(self.ids('/api/task/drafts',self.leader),{self.own_draft})
        # A misleading/cyclic reporting line must not expose managerial work.
        self.c.execute('UPDATE reporting_line SET direct_leader_id=? WHERE user_id=?',(self.leader,self.pm))
        self.assertNotIn(self.pm_approval,self.ids('/api/approvals',self.leader))

    def test_foreign_task_approval_progress_ids_return_not_found(self):
        for path in (f'/api/tasks/{self.pm_pending}',f'/api/approvals/{self.pm_approval}',f'/api/progress?task_id={self.foreign}'):
            self.assertEqual(self.api(path,self.member)[0],404,path)
            self.assertEqual(self.api(path,self.leader)[0],404,path)
        self.assertEqual(self.api(f'/api/tasks/{self.own_pending}',self.member)[0],200)
        self.assertEqual(self.api(f'/api/approvals/{self.own_approval}',self.member)[0],200)

    def test_progress_history_keeps_every_report_and_real_decision(self):
        reports=[]
        for index in range(15):
            rid,aid,_=app.submit_progress(self.c,self.own,self.member,index*5,
                '第'+str(index)+'次反馈 <img src=x onerror=alert(1)>',index==14,'等待接口权限' if index==14 else '',
                '2030-12-01',source='feishu' if index%2 else 'web')
            self.c.execute('UPDATE progress_record SET reported_at=? WHERE id=?',('2030-11-01T10:00:00+08:00',rid))
            if index<12:
                app.decide_approval(self.c,aid,'approved',self.manager)
            elif index==12:
                self.c.execute("UPDATE progress_record SET status='auto_approved',effective_percent=60,confirmed_at=? WHERE id=?",(app.now(),rid))
                self.c.execute("UPDATE approval_record SET status='approved',result_reason='超时自动生效',operated_at=? WHERE id=?",(app.now(),aid))
            elif index==13:
                app.decide_approval(self.c,aid,'rejected',self.manager,reason='请补充验收证据')
            reports.append(rid)
        status,rows=self.api('/api/progress?task_id='+str(self.own),self.member)
        self.assertEqual(status,200)
        self.assertEqual([r['id'] for r in rows],reports[::-1])
        self.assertEqual([r['confirmation_status'] for r in rows[:4]],['pending','rejected','auto_approved','approved'])
        self.assertEqual(rows[1]['status'],'pending')  # Preserve the legacy raw value for existing consumers.
        self.assertEqual(rows[1]['decision_by'],self.manager)
        self.assertEqual(rows[1]['decision_name'],self.c.execute('SELECT name FROM user WHERE id=?',(self.manager,)).fetchone()[0])
        self.assertEqual(rows[1]['result_reason'],'请补充验收证据')
        self.assertTrue(rows[1]['decision_at'])
        self.assertEqual(rows[0]['reported_percent'],70)
        self.assertEqual(rows[0]['block_reason'],'等待接口权限')
        self.assertEqual(rows[0]['reported_by'],self.member)
        self.assertTrue(rows[0]['reporter_name'])
        self.assertIsNone(rows[2]['decision_by'])
        for actor in (self.leader,self.pm,self.manager):
            self.assertEqual(self.api('/api/progress?task_id='+str(self.own),actor)[0],200)
        self.assertEqual(self.api('/api/progress?task_id='+str(self.own)+'&actor_id='+str(self.pm),self.peer)[0],404)

    def test_progress_history_handles_empty_and_legacy_records_without_approval(self):
        self.assertEqual(self.api('/api/progress?task_id='+str(self.own),self.member),(200,[]))
        self.c.execute("INSERT INTO progress_record(task_id,reported_percent,report_text,status,reported_at) VALUES(?,25,'旧版反馈','approved',?)",(self.own,app.now()))
        status,rows=self.api('/api/progress?task_id='+str(self.own),self.member)
        self.assertEqual(status,200)
        self.assertEqual(rows[0]['confirmation_status'],'approved')
        self.assertIsNone(rows[0]['decision_name'])
        self.c.execute("UPDATE task SET work_status='completed' WHERE id=?",(self.own,))
        self.assertEqual(len(self.api('/api/progress?task_id='+str(self.own),self.member)[1]),1)

    def test_guessing_foreign_ids_does_not_permit_any_draft_mutation(self):
        before=[tuple(row) for row in self.c.execute('SELECT * FROM conversation_draft ORDER BY id')]
        for endpoint,body in [('complete',{'title':'越权修改'}),('confirm',{}),('discard',{})]:
            for actor in (self.member,self.leader):
                self.assertEqual(self.api(f'/api/task/draft/{self.pm_draft}/{endpoint}',actor,body)[0],404)
        self.assertEqual(before,[tuple(row) for row in self.c.execute('SELECT * FROM conversation_draft ORDER BY id')])

    def test_team_drafts_are_read_only_but_owner_and_management_can_edit(self):
        items=self.api('/api/task/drafts',self.leader)[1]
        self.assertFalse(next(item for item in items if item['id']==self.own_draft)['can_edit'])
        self.assertEqual(self.api(f'/api/task/draft/{self.own_draft}/complete',self.leader,{'description':'越权修改'})[0],404)
        self.assertEqual(self.api(f'/api/task/draft/{self.own_draft}/complete',self.member,{'description':'本人补充'})[0],200)
        self.assertEqual(self.api(f'/api/task/draft/{self.own_draft}/complete',self.pm,{'description':'管理补充'})[0],200)

    def test_draft_owner_ids_from_legacy_string_slots_keep_the_same_access_rights(self):
        slots=json.loads(self.c.execute('SELECT slots_json FROM conversation_draft WHERE id=?',(self.peer_draft,)).fetchone()[0])
        slots['owner_id']=str(self.member)
        self.c.execute('UPDATE conversation_draft SET slots_json=? WHERE id=?',(json.dumps(slots),self.peer_draft))
        self.assertIn(self.peer_draft,self.ids('/api/task/drafts',self.member))
        self.assertEqual(self.api(f'/api/task/draft/{self.peer_draft}/complete',self.member,{'description':'责任人补充'})[0],200)

    def test_bootstrap_dashboard_and_daily_preview_use_the_same_scope(self):
        status,bootstrap=self.api('/api/bootstrap',self.member);self.assertEqual(status,200)
        self.assertEqual({task['id'] for task in bootstrap['tasks']},{self.own})
        self.assertEqual(bootstrap['dashboard'],self.api('/api/dashboard',self.member)[1])
        self.assertEqual(bootstrap['dashboard']['total'],1)
        self.assertEqual(bootstrap['dashboard']['pending_approval'],1)
        self.assertEqual(bootstrap['dashboard']['unreported'],1)
        # Make tasks visible in the operations preview's "in progress" bucket.
        targets=self.api('/api/ops/daily-preview',self.member)[1]['targets']
        self.assertEqual({task['id'] for target in targets for task in target['tasks']},{self.own})

    def test_forged_actor_parameters_never_override_logged_in_identity(self):
        for base in ('/api/tasks','/api/approvals','/api/task/drafts'):
            self.assertEqual(self.ids(base,self.member),self.ids(base+'?actor_id='+str(self.manager),self.member))
        forged=self.api('/api/bootstrap?actor_id='+str(self.manager),self.member)[1]
        self.assertEqual({task['id'] for task in forged['tasks']},{self.own})
        self.assertEqual(self.api(f'/api/approvals/{self.pm_approval}/confirm',self.member,
                                  {'operator_id':self.manager,'result':'approved'})[0],404)

    def test_draft_creator_is_signed_in_user_even_when_user_id_is_forged(self):
        status,out=self.api('/api/task/draft/parse',self.member,{'text':'本人新草稿','user_id':self.pm})
        self.assertEqual(status,200)
        self.assertEqual(self.c.execute('SELECT user_id FROM conversation_draft WHERE id=?',(out['id'],)).fetchone()[0],self.member)
        self.assertNotIn(out['id'],self.ids('/api/task/drafts',self.peer))

    def test_search_and_duplicate_responses_do_not_expose_hidden_titles_or_ids(self):
        self.assertEqual(self.api('/api/tasks/similar?title='+app.urllib.parse.quote('其他团队私有任务'),self.member)[1],[])
        status,out=self.api('/api/tasks',self.member,{'title':'其他团队私有任务','owner_id':self.peer,'due_at':'2030-11-20T17:00:00+08:00'})
        self.assertEqual(status,409);self.assertEqual(out['duplicates'],[])

    def test_explicitly_assigned_confirmer_can_read_the_relevant_evidence_only(self):
        self.c.execute('UPDATE approval_record SET approver_id=? WHERE id=?',(self.member,self.peer_approval))
        self.assertIn(self.peer_approval,self.ids('/api/approvals',self.member))
        self.assertEqual(self.api(f'/api/tasks/{self.peer_pending}',self.member)[0],200)
        self.assertEqual(self.api(f'/api/tasks/{self.foreign}',self.member)[0],404)

    def test_management_visibility_does_not_grant_confirmation_rights(self):
        for actor in (self.pm,self.manager):
            self.assertEqual(len(self.ids('/api/tasks',actor)),4)
            self.assertEqual(len(self.ids('/api/approvals',actor)),4)
            self.assertEqual(len(self.ids('/api/task/drafts',actor)),3)
        self.assertEqual(self.api(f'/api/approvals/{self.head_approval}/confirm',self.pm,{'result':'approved'})[0],403)

    def test_no_related_data_returns_empty_lists_and_zero_counts(self):
        for endpoint in ('/api/tasks','/api/approvals','/api/task/drafts'):
            self.assertEqual(self.api(endpoint,self.empty)[1],[])
        self.assertTrue(all(value==0 for value in self.api('/api/dashboard',self.empty)[1].values()))

    def test_reporting_relationships_expire_and_future_assignments_do_not_apply_early(self):
        future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,effective_from) VALUES(?,?,?)',(self.peer,self.leader,future))
        self.assertNotIn(self.foreign,self.ids('/api/tasks',self.leader))
        self.c.execute("UPDATE reporting_line SET effective_from='2000-01-01',effective_to=? WHERE user_id=?",(future,self.peer))
        self.assertIn(self.foreign,self.ids('/api/tasks',self.leader))
        self.c.execute("UPDATE reporting_line SET effective_to='2000-01-02' WHERE user_id=?",(self.peer,))
        self.assertNotIn(self.foreign,self.ids('/api/tasks',self.leader))

    def test_feishu_number_queries_and_stale_references_do_not_reveal_foreign_tasks(self):
        for text in (f'查看任务#{self.pm_pending}',f'任务#{self.foreign} 完成20%'):
            reply=app.process_bot_message(self.c,text,self.member,'private-number')
            self.assertIn('无权查看',reply)
            self.assertNotIn('私有任务',reply);self.assertNotIn('私有待确认',reply)
        app.save_bot_task_context(self.c,'transferred',self.member,self.own)
        self.c.execute('UPDATE task SET owner_id=?,title=? WHERE id=?',(self.peer,'转交后新的机密内容',self.own))
        reply=app.process_bot_message(self.c,'该任务完成20%',self.member,'transferred')
        self.assertNotIn('新的机密内容',reply)

    def test_feishu_team_queries_also_exclude_management_tasks_from_malformed_subtrees(self):
        self.c.execute('UPDATE reporting_line SET direct_leader_id=? WHERE user_id=?',(self.leader,self.pm))
        hidden=self.task('PM私有正式任务',self.pm)
        reply=app.process_bot_message(self.c,'查看团队任务',self.leader,'team-list')
        self.assertNotIn('PM私有正式任务',reply)
        reply=app.process_bot_message(self.c,'汇总团队任务进展',self.leader,'team-summary')
        self.assertNotIn('PM私有正式任务',reply)


if __name__=='__main__':unittest.main()
