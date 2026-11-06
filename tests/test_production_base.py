import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import accounts
import app

spec=importlib.util.spec_from_file_location('production_base',Path(app.__file__).parent/'scripts/export-production-base.py')
exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)


class ProductionBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.source=Path(self.temp.name)/'source.db';self.output=Path(self.temp.name)/'private'/'production.db'
        database=patch.object(app,'DB_PATH',self.source);database.start();self.addCleanup(database.stop)
        app.initialize();c=app.db()
        self.manager=c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.member=c.execute("SELECT id FROM user WHERE role='member' LIMIT 1").fetchone()[0]
        self.domain=c.execute("SELECT id FROM org_node WHERE node_type='domain' LIMIT 1").fetchone()[0]
        self.department=c.execute('SELECT id FROM org_department LIMIT 1').fetchone()[0]
        accounts.configure(c,self.manager,'formal.manager','',self.manager)
        c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',('formal-open-id',self.manager))
        self.demo=c.execute("INSERT INTO user(name,role) VALUES('演练·管理PM','pm')").lastrowid
        accounts.configure(c,self.demo,'routine.pm','',self.manager)
        self.position=c.execute('INSERT INTO org_position(name,dept_id) VALUES(?,?)',('正式工程师',self.department)).lastrowid
        demo_position=c.execute('INSERT INTO org_position(name,dept_id) VALUES(?,?)',('[例行演练]工程师',self.department)).lastrowid
        c.executemany('INSERT INTO user_position(user_id,position_id) VALUES(?,?)',[(self.member,self.position),(self.demo,demo_position)])
        c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)',(self.demo,self.manager,self.manager,app.now()))
        for title,owner in (('正式例行模板',self.member),('[例行演练]周报',self.demo),('[自动测试]模板',self.member)):
            c.execute("INSERT INTO task_template(org_node_id,title,owner_id,deliverable,recurrence,estimated_hours,created_at) VALUES(?,?,?,?,'WEEKLY',2,?)",
                      (self.domain,title,owner,'交付物',app.now()))
        c.execute('INSERT INTO task(title,owner_id,org_node_id,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?)',('业务任务',self.member,self.domain,'2030-01-01',app.now(),app.now()))
        c.execute('INSERT INTO login_session VALUES(?,?,?,?,?)',('old-session',self.manager,'2099-01-01',app.now(),app.now()))
        c.execute('INSERT INTO feishu_profile(open_id,fetched_at) VALUES(?,?)',('formal-open-id',app.now()))
        c.execute('INSERT INTO feishu_profile(open_id,fetched_at) VALUES(?,?)',('unrelated-profile',app.now()))
        c.commit();c.close()

    def test_preserves_formal_reference_ids_hashes_and_bindings_without_fixture_or_runtime_data(self):
        before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        report=exporter.export(self.source,self.output)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),before)
        self.assertEqual(report['excluded_fixture_user_ids'],[self.demo])
        with sqlite3.connect(self.source) as source,sqlite3.connect(self.output) as target:
            self.assertEqual(source.execute('SELECT password_hash FROM user_account WHERE user_id=?',(self.manager,)).fetchone(),
                             target.execute('SELECT password_hash FROM user_account WHERE user_id=?',(self.manager,)).fetchone())
            self.assertTrue(accounts.verify_password('123456',target.execute('SELECT password_hash FROM user_account WHERE user_id=?',(self.manager,)).fetchone()[0]))
            self.assertEqual(target.execute('SELECT feishu_open_id FROM user WHERE id=?',(self.manager,)).fetchone()[0],'formal-open-id')
            self.assertEqual(target.execute('SELECT count(*) FROM feishu_profile').fetchone()[0],1)
            self.assertFalse(target.execute('SELECT 1 FROM user WHERE id=?',(self.demo,)).fetchone())
            self.assertFalse(target.execute("SELECT 1 FROM org_position WHERE name LIKE '[例行演练]%'").fetchone())
            self.assertEqual(target.execute('SELECT title FROM task_template').fetchall(),[('正式例行模板',)])
            for table in ('task','task_instance','login_session','notify_log','conversation_draft','audit_log','weekly_report','monthly_report'):
                self.assertEqual(target.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
            self.assertEqual(target.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.output.with_suffix('.db.report.json').stat().st_mode),0o600)

    def test_startup_does_not_reintroduce_fixture_and_preserves_id_high_water_marks(self):
        exporter.export(self.source,self.output)
        with patch.object(app,'DB_PATH',self.output):
            app.initialize();c=app.db()
            self.assertFalse(c.execute("SELECT 1 FROM user WHERE name LIKE '演练·%'").fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM org_position WHERE name LIKE '[例行演练]%'").fetchone())
            new=c.execute("INSERT INTO user(name) VALUES('生产新员工')").lastrowid;self.assertGreater(new,self.demo)
            self.assertEqual(c.execute('SELECT count(*) FROM task').fetchone()[0],0);c.rollback();c.close()

    def test_mixed_template_recipients_remove_only_fixture_people(self):
        c=app.db();tid=c.execute("INSERT INTO task_template(org_node_id,title,owner_id,owner_ids_json,assignment_type,deliverable,recurrence,created_at) VALUES(?,?,NULL,?,'people',?,'WEEKLY',?)",
            (self.domain,'多人正式例行',json.dumps([self.member,self.demo]),'交付',app.now())).lastrowid;c.commit();c.close()
        report=exporter.export(self.source,self.output)
        with sqlite3.connect(self.output) as target:
            row=target.execute('SELECT owner_ids_json,owner_id FROM task_template WHERE id=?',(tid,)).fetchone()
            self.assertEqual(json.loads(row[0]),[self.member]);self.assertEqual(row[1],self.member)
        self.assertEqual(report['recipient_adjustments'],[{'template_id':tid,'removed_fixture_recipients':1}])

    def test_real_configuration_pointing_to_fixture_manager_fails_without_partial_output(self):
        c=app.db();c.execute('UPDATE reporting_line SET approver_id=? WHERE user_id=? AND effective_to IS NULL',(self.demo,self.member));c.commit();c.close()
        with self.assertRaisesRegex(ValueError,'引用了不存在或已排除'):exporter.export(self.source,self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob('.production-base-*')))

    def test_refuses_source_overwrite_and_existing_destination(self):
        with self.assertRaises(ValueError):exporter.export(self.source,self.source)
        exporter.export(self.source,self.output);before=self.output.read_bytes()
        with self.assertRaises(ValueError):exporter.export(self.source,self.output)
        self.assertEqual(self.output.read_bytes(),before)

    def test_legitimate_business_drill_names_are_not_excluded_by_generic_words(self):
        c=app.db();uid=c.execute("INSERT INTO user(name) VALUES('业务演练负责人')").lastrowid;c.commit();c.close()
        exporter.export(self.source,self.output)
        with sqlite3.connect(self.output) as target:self.assertTrue(target.execute('SELECT 1 FROM user WHERE id=?',(uid,)).fetchone())

    def test_expired_assignments_are_excluded_but_formal_positions_remain(self):
        c=app.db();c.execute('INSERT INTO user_position(user_id,position_id,valid_to) VALUES(?,?,?)',(self.member,self.position,'2000-01-01'));c.commit();c.close()
        exporter.export(self.source,self.output)
        with sqlite3.connect(self.output) as target:
            self.assertEqual(target.execute('SELECT count(*) FROM user_position WHERE user_id=?',(self.member,)).fetchone()[0],1)
            self.assertTrue(target.execute('SELECT 1 FROM org_position WHERE id=?',(self.position,)).fetchone())

    def test_legacy_schema_is_upgraded_only_in_the_export_snapshot(self):
        c=app.db();c.executescript('''DROP TABLE task_template;
            CREATE TABLE task_template(id INTEGER PRIMARY KEY AUTOINCREMENT,org_node_id INTEGER NOT NULL,
             title TEXT NOT NULL,description TEXT DEFAULT '',owner_id INTEGER NOT NULL,deliverable TEXT NOT NULL,
             priority TEXT NOT NULL DEFAULT 'P1',recurrence TEXT NOT NULL,due_offset_days INTEGER NOT NULL DEFAULT 0,
             status TEXT NOT NULL DEFAULT 'active',review_at TEXT,created_at TEXT NOT NULL);''')
        c.execute("INSERT INTO task_template(org_node_id,title,owner_id,deliverable,recurrence,created_at) VALUES(?,?,?,?,'WEEKLY',?)",
                  (self.domain,'旧版正式模板',self.member,'交付物',app.now()));c.commit();c.close()
        before=self.source.read_bytes();exporter.export(self.source,self.output)
        self.assertEqual(self.source.read_bytes(),before)
        with sqlite3.connect(self.source) as original,sqlite3.connect(self.output) as target:
            self.assertNotIn('owner_ids_json',{r[1] for r in original.execute('PRAGMA table_info(task_template)')})
            self.assertIn('owner_ids_json',{r[1] for r in target.execute('PRAGMA table_info(task_template)')})


if __name__=='__main__':unittest.main()
