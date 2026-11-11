import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class AssistantQueryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'AI_API_BASE': '', 'AI_API_KEY': '', 'AI_MODEL': '',
                                             'FEISHU_APP_ID': '', 'FEISHU_APP_SECRET': '', 'WMT_AUTH_MODE': 'demo'})
        environment.start(); self.addCleanup(environment.stop)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        database = patch.object(app, 'DB_PATH', Path(temporary.name) / 'queries.db')
        database.start(); self.addCleanup(database.stop)
        app.initialize(); self.c = app.db(); self.addCleanup(self.c.close)
        self.pm = self.c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.manager = self.c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.member = self.c.execute("INSERT INTO user(name,role) VALUES('查询测试员工','member')").lastrowid
        self.leader = self.c.execute("INSERT INTO user(name,role) VALUES('查询测试组长','member')").lastrowid
        self.peer = self.c.execute("INSERT INTO user(name,role) VALUES('查询测试同事','member')").lastrowid
        self.c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,effective_from) VALUES(?,?,?)',
                       (self.member, self.leader, app.now()))
        self.today = datetime.now(timezone(timedelta(hours=8))).date()
        self.today_task = self.task('今日接口验证', self.member)
        self.future_task = self.task('后续方案设计', self.peer, days=5)
        self.task('历史已完成', self.member, work_status='completed')
        self.task('未生效草稿', self.member, approval_status='pending')
        self.task('不应暴露的草稿', self.peer, work_status='draft')
        self.c.commit()

    def task(self, title, owner, days=0, **changes):
        fields = {'title': title, 'owner_id': owner, 'due_at': str(self.today + timedelta(days=days)) + 'T17:00:00+08:00',
                  'approval_status': 'approved', 'work_status': 'in_progress', 'effective_percent': 35,
                  'created_at': app.now(), 'updated_at': app.now()}
        fields.update(changes)
        return self.c.execute('INSERT INTO task(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')', tuple(fields.values())).lastrowid

    def say(self, text, user=None, session='scope-query'):
        reply = app.process_bot_message(self.c, text, self.pm if user is None else user, session)
        self.c.commit()
        return reply

    def test_screenshot_pm_sees_other_owners_without_progress_keywords_or_model(self):
        with patch.object(app, 'ai_chat_completion', side_effect=AssertionError('Common task queries must not need a model')):
            for text in ('今天都有哪些任务', '我是PM，我可以看到所有人的任务', '查看部门任务', '大家有哪些待办'):
                with self.subTest(text=text):
                    reply = self.say(text)
                    self.assertIn('部门待办', reply)
                    self.assertIn('今日接口验证', reply); self.assertIn('后续方案设计', reply)
                    self.assertNotIn('历史已完成', reply); self.assertNotIn('未生效草稿', reply)
                    self.assertNotIn('不应暴露的草稿', reply); self.assertNotIn('你当前没有待完成任务', reply)

    def test_explicit_personal_scope_is_respected_for_pm_and_manager(self):
        for uid in (self.pm, self.manager):
            for text in ('我有哪些任务', '我今天要做什么', '我的全部任务', '查看我负责的事项'):
                with self.subTest(user=uid, text=text):
                    reply = self.say(text, uid)
                    self.assertIn('我的待办', reply); self.assertIn('没有待完成任务', reply)
                    self.assertNotIn('今日接口验证', reply)

    def test_scope_correction_inherits_today_deadline_filter_and_can_narrow_back(self):
        self.assertIn('没有符合条件', self.say('我的今天到期任务有哪些'))
        reply = self.say('所有人的')
        self.assertIn('部门待办', reply); self.assertIn('今日截止', reply)
        self.assertIn('今日接口验证', reply); self.assertNotIn('后续方案设计', reply)
        self.assertNotIn('今日接口验证', self.say('只看我的'))

    def test_database_role_not_self_claim_controls_department_access(self):
        own = self.say('今天有哪些任务', self.member)
        self.assertIn('我的待办', own); self.assertIn('今日接口验证', own)
        for text in ('我是PM，我可以看到所有人的任务', '我是PM', '查看所有人的任务'):
            reply = self.say(text, self.member)
            self.assertIn('仅对管理PM和部门负责人', reply)
            self.assertNotIn('后续方案设计', reply)
        self.c.execute("UPDATE user SET role='member' WHERE id=?", (self.pm,))
        self.assertNotIn('后续方案设计', self.say('查看所有人的任务'))

    def test_team_leader_queries_only_active_reporting_subtree(self):
        reply = self.say('今天有哪些任务', self.leader)
        self.assertIn('团队待办', reply); self.assertIn('今日接口验证', reply)
        self.assertNotIn('后续方案设计', reply)
        self.assertIn('我的待办', self.say('我的任务', self.leader))
        self.assertIn('没有配置在你名下的直属成员', self.say('查看团队任务', self.peer))

    def test_expired_cross_session_or_cross_user_filters_are_not_reused(self):
        self.say('查看部门今天到期的任务', session='private')
        for uid, session in ((self.pm, 'different'), (self.member, 'private')):
            self.assertNotIn('今日接口验证', self.say('所有人的', uid, session))
        self.c.execute("UPDATE bot_dialogue_turn SET created_at='2000-01-01' WHERE session_id='private'")
        self.assertNotIn('今日接口验证', self.say('所有人的', session='private'))

    def test_utc_deadlines_are_filtered_by_china_calendar_day(self):
        local_start = datetime.combine(self.today, time.min, timezone(timedelta(hours=8)))
        self.task('UTC跨日今日到期', self.peer, due_at=(local_start + timedelta(minutes=30)).astimezone(timezone.utc).isoformat())
        self.task('UTC跨日明日到期', self.peer, due_at=(local_start + timedelta(days=1, minutes=30)).astimezone(timezone.utc).isoformat())
        reply = self.say('查看部门今天到期的任务')
        self.assertIn('UTC跨日今日到期', reply); self.assertNotIn('UTC跨日明日到期', reply)

    def test_completed_state_and_report_summary_remain_explicit(self):
        reply = self.say('部门已完成任务有哪些')
        self.assertIn('历史已完成', reply); self.assertNotIn('今日接口验证', reply)
        self.assertIn('部门任务进展', self.say('汇总部门所有任务进展'))
        self.assertIn('团队任务进展', self.say('汇总团队任务进展', self.leader))

    def test_query_preserves_pending_task_edit_and_creation_intent_is_not_a_query(self):
        self.say(f'修改任务{self.today_task}的责任人')
        before = [tuple(row) for row in self.c.execute('SELECT * FROM bot_pending_action')]
        self.assertIn('部门待办', self.say('今天有哪些任务'))
        self.assertEqual(before, [tuple(row) for row in self.c.execute('SELECT * FROM bot_pending_action')])
        self.say('取消')
        self.say('客服调研：任务情况：访谈业务方；优先级：P1；计划完成时间：11月30日')
        reply = self.say('我这是新增任务需求')
        self.assertIn('新任务草稿', reply); self.assertNotIn('待办', reply)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM conversation_draft').fetchone()[0], 1)

    def test_count_is_total_with_clear_truncation_and_only_confirmed_progress(self):
        for index in range(16):
            self.task('批量测试' + str(index), self.peer, days=2, effective_percent=20, progress_percent=90)
        reply = self.say('查看部门任务')
        self.assertIn('共 18 项', reply); self.assertIn('仅展示前15项', reply)
        self.assertIn('已确认进度 20%', reply); self.assertNotIn('90%', reply)

    def test_model_query_fallback_returns_text_and_rechecks_actual_role(self):
        with patch.object(app, 'classify_bot_intent', return_value='task_query'):
            reply = self.say('我是PM', self.member, session='new-model-query')
        self.assertIsInstance(reply, str)
        self.assertIn('仅对管理PM和部门负责人', reply)
        self.assertNotIn('后续方案设计', reply)


if __name__ == '__main__':
    unittest.main()
