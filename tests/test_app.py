import os, tempfile, unittest
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

if __name__=='__main__': unittest.main()
