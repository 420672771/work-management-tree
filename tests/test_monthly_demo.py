import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('monthly_demo', ROOT/'scripts'/'demo-monthly-report.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class MonthlyDemoTests(unittest.TestCase):
    def test_isolated_reproducible_employee_workflows_and_viewer_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            business = Path(temporary)/'business.db'
            output = Path(temporary)/'preview'
            with patch.object(app,'DB_PATH',business):
                app.initialize()
                before = business.read_bytes()
                result = demo.create_demo(output,'2026-10')
                self.assertEqual(business.read_bytes(),before)
                self.assertEqual((result['employees'],result['reports'],result['tasks'],result['actions']),(5,6,18,13))
                bundle = json.loads((output/'report.json').read_text())
                self.assertTrue(bundle['is_demo'])
                self.assertEqual(len(bundle['users']),7)
                self.assertEqual(sum(r['status']=='archived' for r in bundle['reports']),2)
                revised = next(r for r in bundle['reports'] if r['version']==2)
                archived = next(r for r in bundle['reports'] if r['id']==revised['revision_of'])
                self.assertEqual(revised['snapshot'],archived['snapshot'])
                self.assertNotEqual(revised['editorial'],archived['editorial'])
                empty = next(r for r in bundle['reports'] if r['snapshot']['owner']['name'].startswith('孙然'))
                self.assertEqual(empty['snapshot']['tasks'],[])
                html = (output/'index.html').read_text()
                self.assertNotIn('__DEMO_DATA__',html)
                self.assertNotIn('__AUTOSAVE__',html)
                self.assertIn('window.fetch=async',html)
                self.assertIn('sessionStorage',html)
                self.assertIn('/static/monthly-reports.js',html)
                expected = (output/'report.json').read_text()
                demo.create_demo(output,'2026-10')
                self.assertEqual((output/'report.json').read_text(),expected)
                self.assertEqual(business.read_bytes(),before)

    def test_refuses_business_database_and_non_demo_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'preview'
            output.mkdir()
            database = output/'demo.db'
            database.write_bytes(b'not a generated demo database')
            with patch.object(app,'DB_PATH',database), self.assertRaises(ValueError):
                demo.create_demo(output,'2026-10')
            with self.assertRaises(sqlite3.DatabaseError):
                demo.create_demo(output,'2026-10')
            self.assertEqual(database.read_bytes(),b'not a generated demo database')

    def test_demo_route_serves_fixed_fixture_without_opening_business_database(self):
        handler = object.__new__(app.Handler)
        handler.path,handler.command,handler.headers='/monthly-report-demo','GET',{}
        handler.require_auth=lambda:True
        result=[]
        handler.send_bytes=lambda status,content_type,body,cache:result.append((status,content_type,body,cache))
        with patch.object(app,'db',side_effect=AssertionError('demo must not read employee data')):
            handler.do_GET()
        self.assertEqual(result[0][0],200)
        self.assertIn('虚构员工'.encode(),result[0][2])
