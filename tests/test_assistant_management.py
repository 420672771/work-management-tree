import importlib.util
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import accounts
import app


class AssistantManagementTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','AI_API_BASE':'','AI_API_KEY':'','AI_MODEL':'',
            'FEISHU_APP_ID':'test-app','FEISHU_APP_SECRET':'test-secret','FEISHU_VERIFICATION_TOKEN':'test-token'})
        environment.start();self.addCleanup(environment.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        database=patch.object(app,'DB_PATH',Path(temporary.name)/'assistant.db')
        database.start();self.addCleanup(database.stop)
        app.initialize();self.c=app.db();self.addCleanup(self.c.close)
        self.manager=self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm=self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]
        self.c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',('assistant-manager',self.manager))
        self.c.commit()

    def say(self, text, user=None, session=None):
        user=self.manager if user is None else user
        result=app.process_bot_message(self.c,text,user,session or 'feishu:test:'+str(user));self.c.commit()
        return result

    def confirmed(self, text):
        reply=self.say(text);self.assertIn('回复“确认”',reply)
        reply=self.say('确认');self.assertIn('已',reply)
        self.assertNotIn('未执行',reply)
        return reply

    def task(self, owner=None, **changes):
        fields={'title':'助手任务','owner_id':owner or self.member,'org_node_id':None,'due_at':'2030-01-01T17:00:00+08:00',
                'approval_status':'approved','deliverable':'交付记录','created_at':app.now(),'updated_at':app.now()}
        fields.update(changes)
        tid=self.c.execute('INSERT INTO task('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
        self.c.commit();return tid

    REPORT=('AI智能客服：\n'
            '1、任务情况：AI智能客服功能需求调研和方案设计，目前碰到几大卡点，暂且找不到合适解决方案，还在与业务方持续沟通中\n'
            '2、优先级：P1\n3、计划完成时间：11月30日\n4、进度百分比：20%')

    def test_screenshot_report_does_not_plan_deadline_change_or_guess_unrelated_task(self):
        self.assertFalse(app.is_deadline_change_request(self.REPORT))
        for existing in (False,True):
            if existing:tid=self.task(owner=self.pm,title='无关任务')
            reply=self.say(self.REPORT,user=self.pm)
            self.assertIn('未在你可操作的任务清单中匹配到',reply);self.assertIn('是否将它新建为任务',reply)
            self.say('取消',user=self.pm)
            self.assertIn('没有待确认',self.say('确认',user=self.pm))
            self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)
            self.assertEqual(self.c.execute('SELECT count(*) FROM conversation_draft').fetchone()[0],0)
            self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],int(existing))
            if existing:self.assertEqual(self.c.execute('SELECT due_at FROM task WHERE id=?',(tid,)).fetchone()[0],'2030-01-01T17:00:00+08:00')

    def test_labelled_report_matches_own_title_and_submits_progress_without_changing_task_fields(self):
        tid=self.task(owner=self.pm,title='AI智能客服',priority='P0',approver_id=self.manager)
        reply=self.say(self.REPORT,user=self.pm)
        self.assertIn('是否要更新这项任务的进度',reply)
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)
        reply=self.say('确认更新',user=self.pm)
        self.assertIn('已记录《AI智能客服》进度 20%',reply)
        record=self.c.execute('SELECT * FROM progress_record WHERE task_id=?',(tid,)).fetchone()
        self.assertEqual(record['reported_percent'],20);self.assertEqual(record['report_text'],self.REPORT)
        self.assertEqual(record['status'],'pending');self.assertEqual(record['source'],'feishu')
        self.assertEqual(record['expected_finish'],app.parse_due('11月30日'))
        task=self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        self.assertEqual(task['due_at'],'2030-01-01T17:00:00+08:00');self.assertEqual(task['priority'],'P0')
        self.assertEqual(task['effective_percent'],0)
        self.assertEqual(self.c.execute("SELECT approver_id FROM approval_record WHERE biz_type='progress_confirm'").fetchone()[0],self.manager)

    def test_labelled_report_keeps_content_for_bare_number_followup_and_checks_ownership(self):
        tid=self.task(owner=self.pm,id=15,approver_id=self.manager)
        foreign=self.task(owner=self.member)
        self.assertIn('是否将它新建为任务',self.say(self.REPORT,user=self.pm))
        self.assertIn('不是你的责任任务',self.say('#'+str(foreign),user=self.pm))
        self.assertIn('已记录',self.say('#15',user=self.pm))
        record=self.c.execute('SELECT * FROM progress_record').fetchone()
        self.assertEqual(record['task_id'],tid);self.assertEqual(record['report_text'],self.REPORT)
        self.assertEqual(record['reported_percent'],20)
        self.say('#15',user=self.pm)
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],1)

    def test_labelled_report_requires_number_for_duplicate_titles_and_rechecks_state(self):
        first=self.task(owner=self.pm,title='AI智能客服')
        second=self.task(owner=self.pm,title='AI智能客服')
        reply=self.say(self.REPORT,user=self.pm)
        self.assertIn('多条同名或相近任务',reply);self.assertIn('#'+str(first),reply);self.assertIn('#'+str(second),reply)
        self.c.execute("UPDATE task SET work_status='completed' WHERE id=?",(first,));self.c.commit()
        self.assertIn('不支持填报进度',self.say('#'+str(first),user=self.pm))
        self.assertIn('已记录',self.say('#'+str(second),user=self.pm))
        self.assertEqual(self.c.execute('SELECT task_id FROM progress_record').fetchone()[0],second)

    def test_labelled_report_collection_is_isolated_expiring_and_cancellable(self):
        tid=self.task(owner=self.pm)
        self.say(self.REPORT,user=self.pm,session='report-chat')
        self.say('#'+str(tid),user=self.pm,session='another-chat')
        self.say('#'+str(tid),user=self.member,session='report-chat')
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)
        self.say('取消',user=self.pm,session='report-chat');self.say('#'+str(tid),user=self.pm,session='report-chat')
        self.say(self.REPORT,user=self.pm,session='expired-report')
        self.c.execute("UPDATE bot_pending_action SET expire_at='2000-01-01' WHERE session_id='expired-report'");self.c.commit()
        self.say('#'+str(tid),user=self.pm,session='expired-report')
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)

    def test_labelled_report_replaces_stale_edit_and_explicit_creation_only_makes_draft(self):
        tid=self.task(owner=self.pm,title='无关任务')
        self.assertIn('回复“确认”',self.say(f'任务#{tid} 优先级改为P0',user=self.pm))
        self.say(self.REPORT,user=self.pm)
        self.assertIn('新任务草稿',self.say('确认新建',user=self.pm))
        self.assertEqual(self.c.execute('SELECT priority FROM task WHERE id=?',(tid,)).fetchone()[0],'P1')
        reply=self.say('新建任务 '+self.REPORT,user=self.pm)
        self.assertIn('新任务草稿',reply)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM conversation_draft').fetchone()[0],1)
        self.say('#'+str(tid),user=self.pm)
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)

    def test_labelled_report_rejects_out_of_range_progress(self):
        self.task(owner=self.pm,title='AI智能客服')
        for percent in ('101%','-20%'):
            self.assertIn('0%到100%',self.say(self.REPORT.replace('20%',percent),user=self.pm))
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)

    def test_invalid_report_and_new_operations_discard_previous_actions(self):
        tid=self.task(owner=self.pm)
        self.assertIn('回复“确认”',self.say(f'任务#{tid} 优先级改为P0',user=self.pm))
        self.say(self.REPORT.replace('20%','101%'),user=self.pm)
        self.assertIn('没有待确认',self.say('确认',user=self.pm))
        self.say(self.REPORT,user=self.pm)
        self.say('修改任务999999的责任人',user=self.pm)
        self.say('#'+str(tid),user=self.pm)
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)

    def test_explicit_deadline_edits_still_preview_and_require_confirmation(self):
        tid=self.task(owner=self.pm)
        for index,command in enumerate(('截止日期延后到11月30日','计划完成时间调整为11月30日','截止日期改到11月30日')):
            session='deadline-report-regression:'+str(index)
            self.assertIn('回复“确认”',self.say(f'任务#{tid} '+command,user=self.pm,session=session))
            self.assertEqual(self.c.execute('SELECT due_at FROM task WHERE id=?',(tid,)).fetchone()[0],'2030-01-01T17:00:00+08:00')
            self.say('取消',user=self.pm,session=session)

    def test_screenshot_owner_change_collects_missing_name_and_bare_task_number(self):
        tid=self.task(id=15);target=self.c.execute("INSERT INTO user(name,role) VALUES('对话张三','member')").lastrowid;self.c.commit()
        for text in ('修改任务15的责任人','#15'):
            reply=self.say(text,user=self.pm)
            self.assertIn('任务#15',reply);self.assertIn('请提供新的责任人',reply)
            self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=15').fetchone()[0],self.member)
        self.assertIn('请先补齐资料',self.say('确认',user=self.pm))
        preview=self.say('对话张三',user=self.pm);self.assertIn('回复“确认”',preview);self.assertIn('对话张三',preview)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=15').fetchone()[0],self.member)
        self.assertIn('责任人已变更',self.say('确认',user=self.pm))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=15').fetchone()[0],target)
        self.assertIn('没有待确认',self.say('确认',user=self.pm))

    def test_task_edit_can_collect_task_then_value_and_cancel_without_mutation(self):
        tid=self.task(id=15)
        self.assertIn('哪条任务',self.say('修改任务的责任人'))
        self.assertIn('请提供新的责任人',self.say('#15'))
        self.assertIn('回复“确认”',self.say('部门管理PM'))
        self.say('取消');self.assertIn('没有待确认',self.say('确认'))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_owner_changes_understand_number_formats_transfer_and_explicit_member_ids(self):
        tid=self.task(id=15)
        for text in ('任务15','任务编号15','任务编号：15','task #15','#15','＃１５'):
            self.assertEqual(app.task_number(text),15,text)
        commands=['更换任务编号15的负责人为部门管理PM','把任务15转给部门管理PM',
                  '把任务15交给部门管理PM负责',f'#15 责任人：#{self.pm}']
        for index,text in enumerate(commands):
            with self.subTest(text=text):
                self.assertIn('回复“确认”',self.say(text,session='alias:'+str(index)))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_deadline_priority_and_text_edits_accept_short_followups(self):
        tid=self.task()
        for index,(field,value) in enumerate([('截止日期','2030-03-15'),('优先级','P0'),('标题','标题：提交方案'),('详情','等待业务确认后提交验收'),('交付物','评审纪要和问题清单')]):
            session='field:'+str(index)
            with self.subTest(field=field):
                self.assertIn('请提供新的',self.say(f'修改任务{tid}的{field}',session=session))
                self.assertIn('回复“确认”',self.say(value,session=session))
                self.say('取消',session=session)
        self.assertEqual(self.c.execute('SELECT title FROM task WHERE id=?',(tid,)).fetchone()[0],'助手任务')

    def test_unknown_and_duplicate_owner_names_require_clarification_then_explicit_id(self):
        tid=self.task();target=self.c.execute("INSERT INTO user(name) VALUES('重名成员')").lastrowid
        self.c.execute("INSERT INTO user(name) VALUES('重名成员')");self.c.commit()
        self.say(f'修改任务{tid}的责任人')
        self.assertIn('未找到',self.say('不存在的成员'))
        self.assertNotIn('回复“确认”',self.say('重名成员'))
        self.assertIn('回复“确认”',self.say('责任人：#'+str(target)))
        self.say('确认');self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],target)

    def test_labelled_owner_followup_keeps_the_task_and_bare_reference_replaces_old_confirmation(self):
        tid=self.task();self.say(f'修改任务{tid}的责任人')
        self.assertIn(f'任务#{tid}',self.say('责任人改为部门管理PM'))
        self.assertIsNotNone(self.c.execute("SELECT 1 FROM bot_pending_action WHERE status='pending'").fetchone())
        other=self.task(title='另一条任务')
        self.assertIn(f'任务#{other}',self.say('#'+str(other)))
        self.assertIn('没有待确认',self.say('确认'))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_task_collection_survives_queries_but_is_isolated_cancelled_and_expired(self):
        tid=self.task()
        self.say(f'修改任务{tid}的责任人',session='same-chat')
        self.assertNotIn('回复“确认”',self.say('部门管理PM',user=self.member,session='same-chat'))
        self.assertNotIn('回复“确认”',self.say('部门管理PM',session='another-chat'))
        self.say('我有哪些任务',session='same-chat')
        self.assertIn('回复“确认”',self.say('部门管理PM',session='same-chat'));self.say('取消',session='same-chat')
        self.say(f'修改任务{tid}的责任人',session='expired')
        self.c.execute("UPDATE bot_pending_action SET expire_at='2000-01-01' WHERE session_id='expired'");self.c.commit()
        self.assertNotIn('回复“确认”',self.say('部门管理PM',session='expired'))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_collection_rechecks_permissions_and_invalid_new_requests_cannot_confirm_old_changes(self):
        tid=self.task();self.say(f'修改任务{tid}的责任人',user=self.pm)
        self.c.execute("UPDATE user SET role='member' WHERE id=?",(self.pm,));self.c.commit()
        self.assertIn('只有任务责任人',self.say('科技产品中心负责人',user=self.pm))
        self.assertIn('没有待确认',self.say('确认',user=self.pm))
        self.say(f'任务{tid} 责任人改为部门管理PM')
        self.assertIn('未找到任务',self.say('修改任务999999的责任人'))
        self.assertIn('没有待确认',self.say('确认'))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_new_reference_request_replaces_task_collection_and_multiple_tasks_are_not_guessed(self):
        tid=self.task();self.say(f'修改任务{tid}的责任人')
        self.assertIn('回复“确认”',self.say('新建部门 对话切换部门'));self.say('确认')
        self.assertIsNotNone(self.c.execute("SELECT 1 FROM org_department WHERE name='对话切换部门'").fetchone())
        for text in (f'修改任务{tid}和任务999的责任人为部门管理PM',f'修改任务{tid}、999的责任人'):
            self.assertIn('一次明确一条',self.say(text))
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)

    def test_create_all_reference_types_with_accounts_positions_and_routine_template(self):
        self.confirmed('新建部门 数据产品部，负责人：部门管理PM')
        self.confirmed('新建岗位 数据分析师，部门：数据产品部，默认角色：普通成员')
        self.confirmed('新建成员 张三，部门：数据产品部，岗位：数据分析师，直属上级：部门管理PM，账号：zhangsan')
        user=self.c.execute("SELECT * FROM user WHERE name='张三'").fetchone()
        self.assertEqual(user['department'],'数据产品部');self.assertEqual(user['role'],'member')
        account=self.c.execute('SELECT * FROM user_account WHERE user_id=?',(user['id'],)).fetchone()
        self.assertTrue(accounts.verify_password('123456',account['password_hash']))
        self.assertEqual(self.c.execute('SELECT direct_leader_id FROM reporting_line WHERE user_id=? AND effective_to IS NULL',(user['id'],)).fetchone()[0],self.pm)
        self.assertIsNotNone(self.c.execute('SELECT 1 FROM user_position WHERE user_id=? AND is_primary=1',(user['id'],)).fetchone())
        self.confirmed('新建责任域 数据治理，负责人：张三，边界：数据质量管理')
        self.confirmed('新建子责任域 数据检查，上级责任域：数据治理，负责人：张三')
        self.confirmed('新建例行任务模板 每周数据检查，责任域：数据检查，责任人：张三，标准工时：2小时，交付物：检查记录，优先级：P0')
        template=self.c.execute("SELECT * FROM task_template WHERE title='每周数据检查'").fetchone()
        self.assertEqual(template['estimated_hours'],2);self.assertEqual(template['recurrence'],'WEEKLY')
        tasks=app.generate_routines(self.c,'2030-01-07',template_ids=[template['id']]);self.c.commit()
        self.assertEqual(len(tasks),1)
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tasks[0],)).fetchone()[0],user['id'])
        self.assertEqual(self.c.execute("SELECT count(*) FROM audit_log WHERE action='assistant_create'").fetchone()[0],6)

    def test_collect_missing_fields_query_does_not_consume_them_and_cancel_clears(self):
        self.assertIn('补充',self.say('新建岗位 需求分析师'))
        self.assertNotIn('已新建',self.say('确认'))
        self.say('我的待办')
        self.assertIn('回复“确认”',self.say('部门：科技产品中心'))
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_position WHERE name='需求分析师'").fetchone())
        self.assertIn('已新建',self.say('确认'))
        self.assertIn('补充',self.say('新建部门'))
        self.say('取消');self.assertNotIn('回复“确认”',self.say('名称：不应创建'))
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_department WHERE name='不应创建'").fetchone())

    def test_people_routine_template_can_be_collected_and_creates_one_task_per_selected_member(self):
        domain=self.c.execute("SELECT name FROM org_node WHERE node_type='domain' LIMIT 1").fetchone()[0]
        self.assertIn('补充',self.say('新建例行任务模板 多人每周检查，责任域：'+domain+'，标准工时：2，交付物：检查记录'))
        preview=self.say('责任人员：待分配成员、部门管理PM、待分配成员')
        self.assertIn('所选每位员工',preview);self.assertIn('回复“确认”',preview)
        self.assertIn('已新建',self.say('确认'))
        template=self.c.execute("SELECT * FROM task_template WHERE title='多人每周检查'").fetchone()
        self.assertEqual(template['assignment_type'],'people');self.assertIsNone(template['owner_id'])
        self.assertEqual(json.loads(template['owner_ids_json']),[self.member,self.pm])
        tasks=app.generate_routines(self.c,'2026-11-09',template_ids=[template['id']]);self.c.commit()
        self.assertEqual(len(tasks),2)
        reply=self.say('新建例行任务模板 岗位分配已移除，责任域：'+domain+'，岗位：巡检工程师，标准工时：2，交付物：记录')
        self.assertNotIn('回复“确认”',reply)

    def test_newlines_and_natural_user_changes_preserve_existing_password_and_audit(self):
        self.confirmed('请帮我新建一个成员 张三\n部门：科技产品中心\n账号：zhangsan')
        uid=self.c.execute("SELECT id FROM user WHERE name='张三'").fetchone()[0]
        account=dict(self.c.execute('SELECT * FROM user_account WHERE user_id=?',(uid,)).fetchone())
        self.c.execute('INSERT INTO login_session VALUES(?,?,?,?,?)',('old-login',uid,'2099-01-01',app.now(),app.now()));self.c.commit()
        self.confirmed('把张三的角色改为管理PM')
        self.confirmed('修改成员 张三，姓名：李四，账号：lisi')
        new=dict(self.c.execute('SELECT * FROM user_account WHERE user_id=?',(uid,)).fetchone())
        self.assertEqual(new['password_hash'],account['password_hash']);self.assertEqual(new['username'],'lisi')
        self.assertEqual(self.c.execute('SELECT role FROM user WHERE id=?',(uid,)).fetchone()[0],'pm')
        self.assertIsNone(self.c.execute("SELECT 1 FROM login_session WHERE session_id='old-login'").fetchone())
        audit=self.c.execute("SELECT * FROM audit_log WHERE object_id=? AND action='assistant_update' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
        self.assertEqual(audit['actor'],str(self.manager));self.assertNotIn('password',audit['after_json'])

    def test_unbound_and_ordinary_members_cannot_manage_reference_data_or_claim_a_role(self):
        for user in (self.member,None):
            with self.subTest(user=user),patch.object(app,'model_management_proposal',side_effect=AssertionError('must not call model')):
                if user is None:reply=app.process_bot_message(self.c,'新建部门 研发部',None,'unbound')
                else:reply=self.say('新建成员 超级用户，角色：部门负责人',user=user)
                self.assertIn('仅部门负责人',reply)
        self.assertIn('仅部门负责人',self.say('修改成员 待分配成员，角色：部门负责人',user=self.member))
        self.assertIsNone(self.c.execute("SELECT 1 FROM user WHERE name='超级用户'").fetchone())

    def test_confirmation_rechecks_permissions_and_stale_member_data(self):
        self.say('新建部门 研发部',user=self.pm)
        self.c.execute("UPDATE user SET role='member' WHERE id=?",(self.pm,));self.c.commit()
        self.assertIn('未执行',self.say('确认',user=self.pm))
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_department WHERE name='研发部'").fetchone())
        self.say('修改成员 待分配成员，姓名：新名字')
        self.c.execute("UPDATE user SET department='其他部门' WHERE id=?",(self.member,));self.c.commit()
        self.assertIn('发生变化',self.say('确认'))
        self.assertEqual(self.c.execute('SELECT name FROM user WHERE id=?',(self.member,)).fetchone()[0],'待分配成员')

    def test_duplicate_names_conflicting_accounts_and_cyclic_reporting_are_rejected(self):
        self.confirmed('新建成员 张三，账号：zhangsan')
        self.assertNotIn('回复“确认”',self.say('新建成员 李四，账号：zhangsan'))
        self.c.executemany('INSERT INTO user(name) VALUES(?)',[('重名',),('重名',)]);self.c.commit()
        self.assertIn('重名',self.say('修改成员 重名，姓名：名字'))
        uid=self.c.execute("SELECT id FROM user WHERE name='重名' ORDER BY id LIMIT 1").fetchone()[0]
        self.assertIn('回复“确认”',self.say(f'修改成员 #{uid}，姓名：唯一名字'))
        self.say('取消')
        self.assertIn('循环',self.say('修改成员 科技产品中心负责人，直属上级：部门管理PM'))
        self.assertIn('最后一位',self.say('修改成员 科技产品中心负责人，角色：普通成员'))

    def test_atomic_confirmation_rolls_back_partial_account_creation(self):
        self.say('新建成员 应回滚，账号：rollback')
        with patch.object(accounts,'configure',side_effect=accounts.Conflict('模拟账号冲突')):
            self.assertIn('未执行',self.say('确认'))
        self.assertIsNone(self.c.execute("SELECT 1 FROM user WHERE name='应回滚'").fetchone())
        self.assertEqual(self.c.execute('SELECT status FROM bot_pending_action ORDER BY id DESC LIMIT 1').fetchone()[0],'failed')

    def test_cancel_expiry_session_isolation_and_repeated_confirmation(self):
        self.say('新建部门 已取消');self.say('取消');self.say('确认')
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_department WHERE name='已取消'").fetchone())
        self.say('新建部门 已过期')
        self.c.execute("UPDATE bot_pending_action SET expire_at='2000-01-01'");self.c.commit()
        self.assertIn('没有待确认',self.say('确认'))
        self.say('新建部门 隔离部门',session='feishu:one')
        self.assertIn('没有待确认',self.say('确认',session='feishu:two'))
        self.assertIn('已新建',self.say('确认',session='feishu:one'))
        self.assertIn('没有待确认',self.say('确认',session='feishu:one'))
        self.assertEqual(self.c.execute("SELECT count(*) FROM org_department WHERE name='隔离部门'").fetchone()[0],1)

    def test_concurrent_confirmation_executes_once(self):
        self.say('新建部门 并发部门')
        def confirm(_):
            with app.db() as connection:
                reply=app.process_bot_message(connection,'确认',self.manager,'feishu:test:'+str(self.manager))
                connection.commit();return reply
        with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(confirm,range(2)))
        self.assertEqual(sum('已新建' in reply for reply in replies),1)
        self.assertEqual(self.c.execute("SELECT count(*) FROM org_department WHERE name='并发部门'").fetchone()[0],1)

    def test_task_multiple_fields_preview_permission_and_pending_approver_sync(self):
        tid=self.task(approval_status='pending',approver_id=self.manager)
        aid=app.create_approval(self.c,'task_confirm',tid,tid,self.manager);self.c.commit()
        reply=self.say(f'修改任务#{tid}，标题改为新的交付任务，优先级改为P0，责任人改为部门管理PM，截止日期改为2030-02-01')
        self.assertIn('新的交付任务',reply);self.assertIn('P0',reply)
        self.assertEqual(self.c.execute('SELECT title FROM task WHERE id=?',(tid,)).fetchone()[0],'助手任务')
        self.assertIn('已修改',self.say('确认'))
        task=self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        self.assertEqual(task['priority'],'P0');self.assertEqual(task['owner_id'],self.pm)
        self.assertEqual(self.c.execute('SELECT approver_id FROM approval_record WHERE id=?',(aid,)).fetchone()[0],task['approver_id'])
        self.assertIn('只有任务责任人',self.say(f'任务#{tid} 优先级改为P1',user=self.member))

    def test_single_task_fields_show_full_values_and_accept_numbered_owner(self):
        tid=self.task()
        self.assertIn('更清楚的标题',self.say(f'任务#{tid} 标题改成更清楚的标题'))
        self.say('确认')
        self.assertEqual(self.c.execute('SELECT title FROM task WHERE id=?',(tid,)).fetchone()[0],'更清楚的标题')
        self.assertIn('回复“确认”',self.say(f'任务#{tid} 责任人：#{self.pm}'))
        self.say('确认')
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.pm)

    def test_reference_queries_show_ids_without_secrets_and_keep_confirmation(self):
        self.say('新建部门 待确认部门')
        reply=self.say('查看成员')
        self.assertIn('#'+str(self.manager),reply);self.assertNotIn('assistant-manager',reply)
        self.assertIn('已新建',self.say('确认'))
        self.assertIn('待确认部门',self.say('查看部门 待确认部门'))
        self.assertIn('仅部门负责人',self.say('查看成员',user=self.member))

    def test_task_delete_rechecks_staleness_and_invalid_new_request_supersedes_old(self):
        tid=self.task()
        self.say(f'删除任务#{tid}')
        self.c.execute('UPDATE task SET title=? WHERE id=?',('已被修改',tid));self.c.commit()
        self.assertIn('发生变化',self.say('确认'))
        self.say(f'删除任务#{tid}')
        self.say('删除任务#999999')
        self.assertIn('没有待确认',self.say('确认'))
        self.assertIsNotNone(self.c.execute('SELECT 1 FROM task WHERE id=?',(tid,)).fetchone())
        self.assertIn('回复“确认”',self.say(f'删除任务#{tid}',user=self.member))
        self.assertIn('已删除',self.say('确认',user=self.member))
        self.assertIsNone(self.c.execute('SELECT 1 FROM task WHERE id=?',(tid,)).fetchone())

    def test_routine_snapshot_is_protected_and_zero_actual_hours_is_recorded(self):
        tid=self.task(task_type='ROUTINE',routine_week_start='2030-01-07',estimated_hours=2)
        self.assertIn('快照不能修改',self.say(f'任务#{tid} 标准工时改为4'))
        self.assertIn('回复“确认”',self.say(f'任务#{tid} 实际工时改为0',user=self.member))
        self.say('确认',user=self.member)
        task=self.c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        self.assertEqual(task['actual_hours'],0);self.assertTrue(task['routine_hours_updated_at'])

    def test_model_proposals_still_require_allowlisted_fields_and_real_permission(self):
        proposal={'operation':'create','entity':'department','fields':{'name':'模型提取部门'}}
        with patch.object(app,'ai_configured',return_value=True),patch.object(app,'ai_chat_completion',return_value=json.dumps(proposal)):
            self.assertIn('回复“确认”',self.say('帮我新增一个研发部门，叫模型提取部门'))
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_department WHERE name='模型提取部门'").fetchone())
        self.say('确认')
        proposal['fields']['sql']='DROP TABLE user'
        with patch.object(app,'ai_configured',return_value=True),patch.object(app,'ai_chat_completion',return_value=json.dumps(proposal)):
            self.assertNotIn('回复“确认”',self.say('帮我新增一个研发部门，叫注入部门'))
        self.assertGreater(self.c.execute('SELECT count(*) FROM user').fetchone()[0],0)

    def callback(self,text,event_id,token='test-token',send_error=False):
        payload={'header':{'event_id':event_id,'token':token},'event':{'sender':{'sender_id':{'open_id':'assistant-manager'}},
            'message':{'message_type':'text','content':json.dumps({'text':text})}}}
        handler=object.__new__(app.Handler);handler.body=lambda:payload;out=[];handler.json=lambda status,body:out.append((status,body))
        with patch.object(app.FeishuAdapter,'send_text',side_effect=RuntimeError('send failed') if send_error else None) as send:
            handler.handle_feishu_post()
            return out[0],send.call_args

    def test_http_callback_token_deduplication_confirmation_and_send_failure(self):
        response,_=self.callback('新建部门 伪造部门','invalid',token='wrong');self.assertEqual(response[0],403)
        self.assertIsNone(self.c.execute("SELECT 1 FROM bot_pending_action WHERE session_id='feishu:assistant-manager'").fetchone())
        response,send=self.callback('新建部门 回调部门','plan');self.assertEqual(response[0],200);self.assertIn('回复“确认”',send.args[1])
        self.assertIsNone(self.c.execute("SELECT 1 FROM org_department WHERE name='回调部门'").fetchone())
        response,_=self.callback('确认','confirm',send_error=True);self.assertEqual(response[0],200)
        self.callback('确认','confirm')
        self.assertEqual(self.c.execute("SELECT count(*) FROM org_department WHERE name='回调部门'").fetchone()[0],1)

    def test_http_callback_continues_incomplete_task_owner_change(self):
        tid=self.task(id=15)
        for index,text in enumerate(['修改任务15的责任人','#15','责任人改为部门管理PM']):
            response,send=self.callback(text,'dialogue-http-'+str(index));self.assertEqual(response[0],200)
            self.assertIn('请提供新的责任人' if index<2 else '回复“确认”',send.args[1])
            self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member)
        self.callback('确认','dialogue-http-confirm')
        self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.pm)

    def test_http_callback_submits_labelled_report_once(self):
        tid=self.task(owner=self.manager,title='AI智能客服',approver_id=self.pm)
        response,send=self.callback(self.REPORT,'report-http')
        self.assertEqual(response[0],200);self.assertIn('是否要更新这项任务的进度',send.args[1])
        self.callback(self.REPORT,'report-http')
        _,send=self.callback('确认更新','report-http-confirm');self.assertIn('进度 20%',send.args[1])
        self.callback('确认更新','report-http-confirm')
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record WHERE task_id=?',(tid,)).fetchone()[0],1)

    def test_websocket_uses_same_workflow_and_deduplicates_events_without_remote_calls(self):
        path=Path(app.__file__).parent/'scripts'/'feishu-long-connection.py'
        spec=importlib.util.spec_from_file_location('assistant_ws',path)
        ws=importlib.util.module_from_spec(spec);spec.loader.exec_module(ws)
        def receive(text,event_id):
            raw={'header':{'event_id':event_id},'event':{'sender':{'sender_id':{'open_id':'assistant-manager'}},
                 'message':{'message_type':'text','content':json.dumps({'text':text})}}}
            with patch.object(ws.lark.JSON,'marshal',return_value=json.dumps(raw)),patch.object(app.FeishuAdapter,'send_text') as send:
                ws.receive(object());return send.call_args
        self.assertIn('回复“确认”',receive('新建部门 长连接部门','ws-plan').args[1])
        self.assertIn('已新建',receive('确认','ws-confirm').args[1])
        self.assertIsNone(receive('确认','ws-confirm'))
        self.assertEqual(self.c.execute("SELECT count(*) FROM org_department WHERE name='长连接部门'").fetchone()[0],1)

    def test_websocket_continues_incomplete_task_owner_change_without_remote_calls(self):
        tid=self.task(id=15)
        spec=importlib.util.spec_from_file_location('assistant_dialogue_ws',Path(app.__file__).parent/'scripts/feishu-long-connection.py')
        ws=importlib.util.module_from_spec(spec);spec.loader.exec_module(ws)
        for index,text in enumerate(['修改任务15的责任人','#15','部门管理PM','确认']):
            raw={'header':{'event_id':'dialogue-ws-'+str(index)},'event':{'sender':{'sender_id':{'open_id':'assistant-manager'}},'message':{'message_type':'text','content':json.dumps({'text':text})}}}
            with patch.object(ws.lark.JSON,'marshal',return_value=json.dumps(raw)),patch.object(app.FeishuAdapter,'send_text') as send:
                ws.receive(object());self.assertIn('请提供新的责任人' if index<2 else ('回复“确认”' if index==2 else '责任人已变更'),send.call_args.args[1])
            self.assertEqual(self.c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],self.member if index<3 else self.pm)

    def test_websocket_collects_labelled_report_then_number_and_deduplicates_without_remote_calls(self):
        tid=self.task(id=15,owner=self.manager,approver_id=self.pm)
        spec=importlib.util.spec_from_file_location('assistant_report_ws',Path(app.__file__).parent/'scripts/feishu-long-connection.py')
        ws=importlib.util.module_from_spec(spec);spec.loader.exec_module(ws)
        for text,event_id,expected in ((self.REPORT,'report-ws','是否将它新建为任务'),('#15','report-ws-number','进度 20%'),('#15','report-ws-number',None)):
            raw={'header':{'event_id':event_id},'event':{'sender':{'sender_id':{'open_id':'assistant-manager'}},
                 'message':{'message_type':'text','content':json.dumps({'text':text})}}}
            with patch.object(ws.lark.JSON,'marshal',return_value=json.dumps(raw)),patch.object(app.FeishuAdapter,'send_text') as send:
                ws.receive(object())
                if expected:self.assertIn(expected,send.call_args.args[1])
                else:send.assert_not_called()
        record=self.c.execute('SELECT * FROM progress_record').fetchall()
        self.assertEqual(len(record),1);self.assertEqual(record[0]['task_id'],tid)
        self.assertEqual(record[0]['report_text'],self.REPORT)


if __name__=='__main__':unittest.main()
