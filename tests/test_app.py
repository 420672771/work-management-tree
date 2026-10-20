import base64, os, struct, tempfile, unittest
from pathlib import Path
os.environ['WMT_DB']=str(Path(tempfile.mkdtemp())/'test.db')
import sys; sys.path.insert(0,str(Path(__file__).parents[1]))
import app

class WorkTreeTests(unittest.TestCase):
    def setUp(self): app.initialize()
    def test_seed_and_ai_draft_requires_confirmation(self):
        c=app.db(); self.assertGreater(c.execute('select count(*) from org_node').fetchone()[0],0); c.close()
        x=app.ai_draft('请在2026-10-20前完成产品方案'); self.assertTrue(x['requires_human_confirmation']); self.assertEqual(x['due_at'][:10],'2026-10-20')
    def test_task_requires_owner_and_deliverable(self):
        c=app.db();
        try:
            with self.assertRaises(Exception): c.execute('insert into task(title,owner_id,due_at) values(?,?,?)',('x',1,'2026-10-20'))
        finally: c.close()
    def test_time_status(self):
        c=app.db(); c.execute("insert into task(title,owner_id,due_at,deliverable,created_at,updated_at) values(?,?,?,?,?,?)",('late',1,'2020-01-01T00:00:00+00:00','x',app.now(),app.now())); c.commit(); x=app.task_view(c,c.execute('select * from task').fetchone()); self.assertEqual(x['time_status'],'overdue'); c.close()
    def test_wecom_is_safe_without_credentials(self):
        for key in ('WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET'): os.environ.pop(key, None)
        result=app.WeComAdapter().send('tester','test message')
        self.assertEqual(result['mode'],'dry-run')
        self.assertFalse(result['sent'])
    def test_task_view_preserves_separate_risk_and_time_states(self):
        c=app.db()
        try:
            c.execute("insert into task(title,owner_id,due_at,deliverable,risk_status,created_at,updated_at) values(?,?,?,?,?,?,?)",('blocked',1,'2020-01-01T00:00:00+00:00','x','blocked',app.now(),app.now())); c.commit()
            x=app.task_view(c,c.execute("select * from task where title='blocked'").fetchone())
            self.assertEqual(x['risk_status'],'blocked')
            self.assertEqual(x['time_status'],'overdue')
        finally: c.close()
    def test_v2_reporting_line_and_progress_confirmation_models(self):
        c=app.db()
        try:
            manager=c.execute("select id from user where role='manager' limit 1").fetchone()[0]
            owner=c.execute("select id from user where id<>? limit 1",(manager,)).fetchone()[0]
            self.assertEqual(app.approver_for(c,owner),manager)
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,approver_id,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('v2',owner,'2030-01-01T00:00:00+00:00','x','approved',manager,app.now(),app.now())).lastrowid
            rid=c.execute("insert into progress_record(task_id,reported_percent,report_text,reported_at) values(?,?,?,?)",(tid,60,'完成核心流程',app.now())).lastrowid
            aid=app.create_approval(c,'progress_confirm',rid,tid,manager); c.commit()
            self.assertEqual(c.execute('select status from approval_record where id=?',(aid,)).fetchone()[0],'pending')
            self.assertEqual(c.execute('select effective_percent from progress_record where id=?',(rid,)).fetchone()[0],None)
        finally: c.close()
    def test_routine_generation_is_idempotent(self):
        c=app.db()
        try:
            owner=2; node=c.execute("select id from org_node where node_type='domain' limit 1").fetchone()[0]
            c.execute("insert into task_template(org_node_id,title,owner_id,deliverable,recurrence,created_at) values(?,?,?,?,?,?)",(node,'每日站会',owner,'站会纪要','DAILY',app.now()))
            self.assertEqual(len(app.generate_routines(c,'2030-01-01')),1)
            self.assertEqual(len(app.generate_routines(c,'2030-01-01')),0)
            self.assertEqual(c.execute("select count(*) from task where task_type='ROUTINE'").fetchone()[0],1)
        finally: c.close()
    def test_daily_jobs_mark_unreported_without_duplicate_failures(self):
        c=app.db()
        try:
            c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,work_status,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('日报验收',2,'2030-01-01T00:00:00+00:00','日报','approved','in_progress',app.now(),app.now()))
            result=app.run_daily_jobs(c,'2030-01-01','all')
            self.assertGreaterEqual(result['morning'],1)
            self.assertGreaterEqual(result['redboard'],1)
            self.assertGreater(c.execute("select count(*) from daily_report_status where report_date='2030-01-01'").fetchone()[0],0)
        finally: c.close()
    def test_conversation_draft_slots_require_owner_and_deadline(self):
        slots,missing=app.draft_slots('请完成会议纪要')
        self.assertIn('owner_id',missing)
        self.assertIn('due_at',missing)
        slots,missing=app.draft_slots('请在2030-01-01完成会议纪要',2)
        self.assertFalse(missing)
        self.assertEqual(slots['due_at'][:10],'2030-01-01')
    def test_parse_due_understands_chinese_weekday(self):
        from datetime import datetime
        due=app.parse_due('本周四之前完成部门总结报告')
        self.assertIsNotNone(due)
        self.assertEqual(datetime.fromisoformat(due).weekday(),3)
    def test_friendly_slot_labels_never_returns_internal_names(self):
        labels=app.friendly_slot_labels(['owner_id','due_at','unexpected_key'])
        self.assertEqual(labels,'任务责任人、截止日期、必要信息')
        self.assertNotIn('owner_id',labels)
        self.assertNotIn('due_at',labels)
    def test_discard_draft_hides_it_but_keeps_audit_record(self):
        c=app.db()
        try:
            did=c.execute("insert into conversation_draft(session_id,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('test','无需创建','{}','[]','preview','2030-01-01T00:00:00+00:00',app.now(),app.now())).lastrowid
            self.assertTrue(app.discard_draft(c,did))
            c.commit()
            self.assertEqual(c.execute('select status from conversation_draft where id=?',(did,)).fetchone()[0],'discarded')
            self.assertEqual(c.execute("select count(*) from audit_log where object_type='conversation_draft' and object_id=? and action='discard'",(did,)).fetchone()[0],1)
        finally: c.close()
    def test_expired_progress_is_auto_confirmed(self):
        c=app.db()
        try:
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,work_status,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('超时进度',2,'2030-01-01T00:00:00+00:00','x','approved','in_progress',app.now(),app.now())).lastrowid
            rid=c.execute("insert into progress_record(task_id,reported_percent,report_text,reported_at) values(?,?,?,?)",(tid,75,'等待确认',app.now())).lastrowid
            aid=app.create_approval(c,'progress_confirm',rid,tid,1); c.execute("update approval_record set created_at='2020-01-01T00:00:00+00:00' where id=?",(aid,)); result=app.run_daily_jobs(c,'2030-01-01','approval')
            self.assertEqual(result['auto_confirmed_progress'],1)
            self.assertEqual(c.execute('select effective_percent from task where id=?',(tid,)).fetchone()[0],75)
        finally: c.close()
    def test_feishu_adapter_is_safe_without_configuration(self):
        for key in ('FEISHU_APP_ID','FEISHU_APP_SECRET','FEISHU_VERIFICATION_TOKEN'): os.environ.pop(key,None)
        self.assertFalse(app.FeishuAdapter().configured())
        self.assertEqual(app.FeishuAdapter().send_text('ou_test','hello')['mode'],'dry-run')
    def test_wecom_callback_decrypts_and_maps_feedback(self):
        from Crypto.Cipher import AES
        key=base64.b64encode(b'0123456789abcdef').decode().decode() if False else base64.b64encode(b'0123456789abcdef'*2).decode()[:43]
        # 32-byte AES key encoded as the 43-character Enterprise WeChat key.
        os.environ.update({'WECOM_TOKEN':'test-token','WECOM_AES_KEY':key,'WECOM_CORP_ID':'corp-test'})
        codec=app.WeComCallback(); self.assertTrue(codec.configured())
        c=app.db(); c.execute("insert into task(title,owner_id,due_at,deliverable,created_at,updated_at) values(?,?,?,?,?,?)",('callback-task',1,'2030-01-01T00:00:00+00:00','x',app.now(),app.now())); tid=c.execute('select last_insert_rowid()').fetchone()[0]; c.commit()
        xml=f'<xml><FromUserName>wecom-user-1</FromUserName><MsgType>text</MsgType><Content>任务#{tid} 已完成，预计2026-10-20</Content></xml>'.encode()
        raw=b'0123456789abcdef'+struct.pack('>I',len(xml))+xml+b'corp-test'; pad=32-(len(raw)%32); raw+=bytes([pad])*pad
        decoded=base64.b64decode(key+'='); encrypted=base64.b64encode(AES.new(decoded,AES.MODE_CBC,decoded[:16]).encrypt(raw)).decode()
        self.assertEqual(codec.decrypt(encrypted),xml.decode()); self.assertEqual(codec.task_id_from_text(f'任务#{tid} 已完成'),tid); result=codec.handle_message(c,xml.decode()); c.commit(); self.assertEqual(result['task_id'],tid); self.assertEqual(c.execute('select count(*) from feedback where task_id=?',(tid,)).fetchone()[0],1); c.close()

if __name__=='__main__': unittest.main()
