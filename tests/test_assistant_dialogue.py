import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AssistantDialogueTests(unittest.TestCase):
    BRIEF=('AI智能客服：\n1、任务情况：AI智能客服功能需求调研和方案设计，目前碰到几大卡点，'
           '暂且找不到合适解决方案，还在与业务方持续沟通中\n2、优先级：P1\n3、计划完成时间：11月30日')

    def setUp(self):
        env=patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'0','AI_API_BASE':'','AI_API_KEY':'','AI_MODEL':'',
            'FEISHU_APP_ID':'test-app','FEISHU_APP_SECRET':'test-secret','FEISHU_VERIFICATION_TOKEN':'test-token'})
        env.start();self.addCleanup(env.stop)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        database=patch.object(app,'DB_PATH',Path(tmp.name)/'dialogue.db');database.start();self.addCleanup(database.stop)
        app.initialize();self.c=app.db();self.addCleanup(self.c.close)
        self.pm=self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=self.c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]

    def say(self,text,user=None,session='dialogue'):
        reply=app.process_bot_message(self.c,text,user or self.pm,session);self.c.commit();return reply

    def draft(self):
        rows=self.c.execute('SELECT * FROM conversation_draft ORDER BY id').fetchall()
        self.assertEqual(len(rows),1)
        return rows[0],json.loads(rows[0]['slots_json'])

    def model_env(self):
        return patch.dict(os.environ,{'AI_API_BASE':'https://model.example/v1','AI_API_KEY':'not-a-real-key','AI_MODEL':'test-model'})

    def test_exact_screenshot_corrects_intent_and_reuses_the_previous_report(self):
        report=self.BRIEF+'\n4、进度百分比：20%'
        self.assertIn('任务编号',self.say(report))
        reply=self.say('这是一条新增任务')
        self.assertIn('新任务草稿：AI智能客服',reply);self.assertNotIn('请补充：截止日期',reply)
        before,slots=self.draft();self.assertEqual(slots['title'],'AI智能客服')
        self.assertEqual(slots['due_at'],app.parse_due('11月30日'));self.assertEqual(slots['priority'],'P1')
        self.assertIn('几大卡点',slots['description']);self.assertIn('20%',before['raw_text'])
        reply=self.say(self.BRIEF)
        after,slots=self.draft();self.assertEqual(after['id'],before['id']);self.assertEqual(slots['title'],'AI智能客服')
        self.assertIn('资料已补齐',reply)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM progress_record').fetchone()[0],0)

    def test_ambiguous_brief_asks_intent_then_uses_retained_information(self):
        question=self.say(self.BRIEF)
        self.assertIn('新建任务，还是更新已有任务',question)
        self.assertEqual(self.c.execute('SELECT count(*) FROM conversation_draft').fetchone()[0],0)
        self.assertIn('资料已补齐',self.say('这是新增任务'))
        _,slots=self.draft();self.assertEqual(slots['title'],'AI智能客服')
        self.assertEqual(slots['due_at'],app.parse_due('11月30日'))

    def test_missing_information_is_collected_over_multiple_turns_without_duplicates(self):
        self.assertIn('任务标题',self.say('新建任务'))
        self.assertIn('截止日期',self.say('AI智能客服'))
        self.assertIn('资料已补齐',self.say('11月30日'))
        self.say('优先级：P0');self.say('临时任务');self.say('负责人是部门管理PM')
        self.say('交付物：需求方案和评审纪要');self.say('任务情况：与业务方确认方案')
        row,slots=self.draft()
        self.assertEqual(slots['title'],'AI智能客服');self.assertEqual(slots['priority'],'P0')
        self.assertEqual(slots['task_type'],'ADHOC');self.assertEqual(slots['owner_id'],self.pm)
        self.assertEqual(slots['deliverable'],'需求方案和评审纪要');self.assertEqual(slots['description'],'与业务方确认方案')
        self.assertEqual(row['status'],'preview');self.assertEqual(json.loads(row['missing_slots']),[])
        self.assertIn('网页',self.say('确认'));self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],0)

    def test_queries_do_not_consume_draft_collection_and_new_operations_keep_their_permissions(self):
        self.say('新建任务 客服需求设计')
        self.assertIn('没有待完成任务',self.say('我有哪些任务'))
        self.assertIn('资料已补齐',self.say('11月30日'))
        self.assertIn('仅部门负责人',self.say('新建成员 越权人员',user=self.member))
        self.assertIsNone(self.c.execute("SELECT 1 FROM user WHERE name='越权人员'").fetchone())

    def test_cancel_discards_current_draft_and_cannot_resurrect_cancelled_material(self):
        self.say(self.BRIEF);self.say('这是新增任务');self.say('取消')
        row,_=self.draft();self.assertEqual(row['status'],'discarded')
        self.assertNotIn('资料已补齐',self.say('11月30日'))
        reply=self.say('新建任务');self.assertIn('待补充任务标题',reply)
        rows=self.c.execute('SELECT slots_json FROM conversation_draft ORDER BY id').fetchall()
        self.assertEqual(len(rows),2);self.assertIsNone(json.loads(rows[-1][0])['title'])

    def test_collection_and_model_history_are_isolated_by_user_and_session(self):
        self.say('新建任务 私有事项',session='one')
        self.say('11月30日',session='two');self.say('11月30日',user=self.member,session='one')
        _,slots=self.draft();self.assertIsNone(slots['due_at'])
        with self.model_env(),patch.object(app,'ai_chat_completion',return_value='{"intent":"complete_draft","fields":{"due_at":"2030-11-30"}}') as model:
            self.say('到月底办完',session='one')
        messages=model.call_args.args[0]
        self.assertTrue(any(m['role']=='assistant' for m in messages));self.assertIn('私有事项',json.dumps(messages,ensure_ascii=False))
        self.assertEqual(sum(m['role']=='user' and m['content']=='11月30日' for m in messages),0)

    def test_expired_or_discarded_draft_is_not_updated(self):
        self.say('新建任务 过期事项')
        self.c.execute("UPDATE conversation_draft SET expire_at='2000-01-01'")
        self.c.execute("UPDATE bot_pending_action SET expire_at='2000-01-01'");self.c.commit()
        self.assertNotIn('资料已补齐',self.say('11月30日'))
        _,slots=self.draft();self.assertIsNone(slots['due_at'])

    def test_model_receives_multi_turn_context_and_fields_are_merged(self):
        self.say('新建任务 客服需求设计')
        self.say('优先级：P1')
        with self.model_env(),patch.object(app,'ai_chat_completion',return_value='{"intent":"complete_draft","fields":{"description":"持续与业务方沟通"}}') as model:
            self.say('现在还在跟业务那边沟通，先记上这个情况')
        messages=model.call_args.args[0];context=messages[0]['content']
        self.assertIn('客服需求设计',context);self.assertIn('missing',context)
        self.assertIn('优先级：P1',json.dumps(messages,ensure_ascii=False))
        self.assertEqual(messages[-1]['content'],'现在还在跟业务那边沟通，先记上这个情况')
        _,slots=self.draft();self.assertEqual(slots['description'],'持续与业务方沟通')
        self.assertEqual(slots['priority'],'P1');self.assertIsNone(slots['due_at'])
        trace=self.c.execute("SELECT after_json FROM audit_log WHERE object_type='assistant_model' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(trace[0])['intent'],'complete_draft')

    def test_model_outage_keeps_draft_and_asks_for_missing_fields_without_leaking_error(self):
        self.say('新建任务 客服需求设计')
        with self.model_env(),patch.object(app,'ai_chat_completion',side_effect=TimeoutError('secret-must-not-appear')):
            reply=self.say('优先级：P1')
        self.assertIn('截止日期',reply);self.assertNotIn('secret-must-not-appear',reply)
        _,slots=self.draft();self.assertEqual(slots['title'],'客服需求设计');self.assertEqual(slots['priority'],'P1')
        trace=self.c.execute("SELECT action,after_json FROM audit_log WHERE object_type='assistant_model' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(trace[0],'dialogue_unavailable');self.assertNotIn('secret-must-not-appear',trace[1])

    def test_invalid_model_fields_cannot_change_identity_or_permissions(self):
        self.say('新建任务 客服需求设计')
        with self.model_env(),patch.object(app,'ai_chat_completion',return_value='{"intent":"complete_draft","fields":{"role":"manager","owner_id":1}}'):
            self.say('11月30日')
        _,slots=self.draft();self.assertEqual(slots['owner_id'],self.pm);self.assertNotIn('role',slots)
        self.assertEqual(slots['due_at'],app.parse_due('11月30日'))

    def test_invalid_owner_and_date_prompt_for_correction_instead_of_using_defaults(self):
        self.say('新建任务 客服需求设计')
        self.assertIn('未找到',self.say('负责人：不存在的成员'))
        _,slots=self.draft();self.assertIsNone(slots['owner_id'])
        self.assertIn('截止日期未识别',self.say('截止日期：11月31日'))
        self.say('部门管理PM');self.assertIn('资料已补齐',self.say('11月30日'))

    def test_explicit_second_task_starts_separate_draft(self):
        self.say('新建任务 客服需求设计');self.say('新建任务 服务验收')
        rows=self.c.execute('SELECT slots_json FROM conversation_draft ORDER BY id').fetchall()
        self.assertEqual([json.loads(row[0])['title'] for row in rows],['客服需求设计','服务验收'])

    def test_new_task_with_date_in_same_message_keeps_the_task_title(self):
        self.assertIn('资料已补齐',self.say('新建任务 11月30日前完成客服方案'))
        _,slots=self.draft();self.assertTrue(slots['title']);self.assertEqual(slots['due_at'],app.parse_due('11月30日'))

    def task(self):
        tid=self.c.execute('INSERT INTO task(title,owner_id,due_at,description,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            ('AI智能客服',self.pm,'2030-01-01T17:00:00+08:00','原任务详情',app.now(),app.now())).lastrowid
        self.c.commit();return tid

    def test_existing_progress_collects_target_and_percentage_with_original_report(self):
        tid=self.task()
        self.say(self.BRIEF);self.assertIn('任务编号',self.say('更新已有任务'))
        self.assertIn('填报进度',self.say('#'+str(tid)))
        self.assertIn('完成比例',self.say('填报进度'));self.assertIn('0%到100%',self.say('120%'))
        self.assertIn('进度 20%',self.say('20%'))
        record=self.c.execute('SELECT * FROM progress_record').fetchone()
        self.assertEqual(record['task_id'],tid);self.assertEqual(record['reported_percent'],20)
        self.assertIn('几大卡点',record['report_text'])
        self.assertEqual(self.c.execute('SELECT due_at FROM task WHERE id=?',(tid,)).fetchone()[0],'2030-01-01T17:00:00+08:00')

    def test_existing_content_changes_preview_then_confirm_and_recheck_permissions(self):
        tid=self.task()
        self.say(self.BRIEF);self.say('更新任务#'+str(tid))
        preview=self.say('修改任务资料')
        self.assertIn('回复“确认”',preview);self.assertIn('优先级：P1',preview)
        self.assertEqual(self.c.execute('SELECT description FROM task WHERE id=?',(tid,)).fetchone()[0],'原任务详情')
        self.assertIn('已修改',self.say('确认'))
        self.assertIn('几大卡点',self.c.execute('SELECT description FROM task WHERE id=?',(tid,)).fetchone()[0])

    def test_intent_confirmation_does_not_guess_creation_or_update(self):
        self.say(self.BRIEF);self.assertIn('还需要明确',self.say('确认'))
        self.assertEqual(self.c.execute('SELECT count(*) FROM conversation_draft').fetchone()[0],0)

    def test_unknown_input_asks_a_question_and_model_failure_does_not_retry_other_model_paths(self):
        with self.model_env(),patch.object(app,'ai_chat_completion',side_effect=TimeoutError('unavailable')) as model:
            reply=self.say('帮我处理一下刚才的事情')
        self.assertEqual(model.call_count,1);self.assertIn('新建任务，还是更新已有任务',reply)
        self.assertEqual(self.c.execute('SELECT count(*) FROM task').fetchone()[0],0)

    def test_legacy_screenshot_draft_is_repaired_in_place_on_next_supplement(self):
        self.c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('dialogue',self.pm,'feishu','这是一条新增任务',json.dumps({'title':'这是一条新增任务','owner_id':self.pm,'due_at':None}),
             '["due_at"]','collecting','2099-01-01',app.now(),app.now()));self.c.commit()
        self.assertIn('资料已补齐',self.say(self.BRIEF))
        _,slots=self.draft();self.assertEqual(slots['title'],'AI智能客服')

    def test_new_blank_task_does_not_reuse_previous_reference_data(self):
        self.say('新建岗位 数据工程师，部门：科技产品中心，默认角色：普通成员')
        self.say('确认')
        reply=self.say('新建任务')
        self.assertIn('待补充任务标题',reply)
        _,slots=self.draft();self.assertIsNone(slots['title'])

    def test_model_understands_natural_intent_correction_using_retained_material(self):
        self.say(self.BRIEF)
        with self.model_env(),patch.object(app,'ai_chat_completion',return_value='{"intent":"create_task","fields":{"title":"AI智能客服"}}'):
            reply=self.say('不是要改日期，是把上面这项工作记成一条新任务')
        self.assertIn('资料已补齐',reply)
        row,slots=self.draft();self.assertIn('几大卡点',row['raw_text'])
        self.assertEqual(slots['title'],'AI智能客服');self.assertEqual(slots['due_at'],app.parse_due('11月30日'))

    def test_websocket_corrects_intent_and_completes_same_draft_without_remote_calls(self):
        self.c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',('dialogue-user',self.pm));self.c.commit()
        spec=importlib.util.spec_from_file_location('dialogue_ws',Path(app.__file__).parent/'scripts/feishu-long-connection.py')
        ws=importlib.util.module_from_spec(spec);spec.loader.exec_module(ws)
        for i,(text,expected) in enumerate(((self.BRIEF,'新建任务，还是更新已有任务'),('这是一条新增任务','新任务草稿：AI智能客服'),('优先级：P0','优先级：P0'))):
            raw={'header':{'event_id':'dialogue-'+str(i)},'event':{'sender':{'sender_id':{'open_id':'dialogue-user'}},
                'message':{'message_type':'text','content':json.dumps({'text':text})}}}
            with patch.object(ws.lark.JSON,'marshal',return_value=json.dumps(raw)),patch.object(app.FeishuAdapter,'send_text') as send:
                ws.receive(object());self.assertIn(expected,send.call_args.args[1])
        _,slots=self.draft();self.assertEqual(slots['title'],'AI智能客服');self.assertEqual(slots['priority'],'P0')


if __name__=='__main__':unittest.main()
