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
