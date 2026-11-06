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


if __name__=='__main__':unittest.main()
