import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dev_server


class ReloadTests(unittest.TestCase):
    def test_only_runtime_python_content_changes_are_watched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'app.py').write_text('VALUE=1\n')
            first = dev_server.sources(root)
            for folder in ('static','data','logs','tests','scripts','artifacts','.git'):
                (root/folder).mkdir()
                (root/folder/'ignored.txt').write_text('change')
                (root/folder/'ignored.py').write_text('not python')
            self.assertEqual(dev_server.sources(root), first)
            (root/'app.py').touch()
            self.assertEqual(dev_server.sources(root), first)
            (root/'app.py').write_text('VALUE=2\n')
            self.assertNotEqual(dev_server.sources(root), first)

    def test_changes_must_settle_and_multiple_writes_are_coalesced(self):
        detector = dev_server.ChangeDetector({'app.py':'old'}, settle=2)
        self.assertFalse(detector.ready({'app.py':'a'}, 0))
        self.assertFalse(detector.ready({'app.py':'b'}, 1))
        self.assertFalse(detector.ready({'app.py':'b'}, 2))
        self.assertTrue(detector.ready({'app.py':'b'}, 3))
        detector.acknowledge({'app.py':'b'})
        self.assertFalse(detector.ready({'app.py':'b'}, 10))

    def test_syntax_and_import_failures_are_detected_before_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'app.py').write_text('broken (\n')
            with self.assertRaises(SyntaxError):dev_server.validate(root,dev_server.sources(root))
            (root/'app.py').write_text('import missing_worktree_test_module\n')
            with self.assertRaises(RuntimeError):dev_server.validate(root,dev_server.sources(root))

    def test_import_probe_uses_isolated_database_and_does_not_write_pyc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'app.py').write_text('import os\nfrom pathlib import Path\nPath(os.environ["WMT_DB"]).touch()\n')
            dev_server.validate(root,dev_server.sources(root))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['app.py'])

    def test_actual_worker_reloads_preserves_environment_and_survives_bad_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);events=root/'events.jsonl';output=root/'supervisor.log'
            def code(version):
                return f'''VERSION={version!r}
if __name__ == '__main__':
    import json,os,time
    from pathlib import Path
    with Path({str(events)!r}).open('a') as stream:
        stream.write(json.dumps({{'version':VERSION,'pid':os.getpid(),'port':os.getenv('PORT'),'db':os.getenv('WMT_DB'),'child':os.getenv('WMT_RELOAD_CHILD')}})+'\\n')
    if VERSION=='crash':raise RuntimeError('test startup failure')
    while True:time.sleep(.05)
'''
            (root/'app.py').write_text(code('one'))
            env=dict(os.environ,PORT='55123',WMT_DB=str(root/'unchanged.db'),PYTHONDONTWRITEBYTECODE='1')
            with output.open('w') as log:
                process=subprocess.Popen([sys.executable,'-B',str(ROOT/'dev_server.py'),'--root',str(root),'--interval','.03','--settle','.1'],env=env,stdout=log,stderr=log)
                def until(condition):
                    end=time.monotonic()+7
                    while time.monotonic()<end:
                        if condition():return
                        if process.poll() is not None:self.fail(output.read_text())
                        time.sleep(.02)
                    self.fail('Timed out: '+output.read_text())
                def rows():return [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
                try:
                    until(lambda:len(rows())==1)
                    first=rows()[0]
                    (root/'app.py').write_text('broken (\n')
                    until(lambda:'检查未通过' in output.read_text())
                    self.assertEqual(len(rows()),1);os.kill(first['pid'],0)
                    (root/'app.py').write_text(code('two'))
                    until(lambda:len(rows())==2)
                    second=rows()[1]
                    self.assertNotEqual(first['pid'],second['pid'])
                    self.assertEqual(second['version'],'two')
                    self.assertEqual(second['port'],'55123')
                    self.assertEqual(second['db'],str(root/'unchanged.db'))
                    self.assertEqual(second['child'],'1')
                    self.assertFalse((root/'unchanged.db').exists())
                    (root/'app.py').write_text(code('crash'))
                    until(lambda:len(rows())==3 and '继续监听源码' in output.read_text())
                    self.assertIsNone(process.poll())
                    (root/'app.py').write_text(code('recovered'))
                    until(lambda:len(rows())==4)
                    self.assertEqual(rows()[-1]['version'],'recovered')
                finally:
                    process.terminate();process.wait(timeout=5)
                with self.assertRaises(ProcessLookupError):os.kill(rows()[-1]['pid'],0)


class ConfigureTests(unittest.TestCase):
    def test_enable_disable_and_failure_rollback_without_changing_unit_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);scripts=root/'scripts';scripts.mkdir();tools=root/'tools';tools.mkdir()
            shutil.copy(ROOT/'scripts'/'configure-auto-reload.sh',scripts/'configure-auto-reload.sh')
            (scripts/'run-dev-server.py').write_text('print("test validation passed")\n')
            systemctl=tools/'systemctl'
            systemctl.write_text('''#!/bin/sh
case "$*" in
  *LoadState*) echo loaded;;
  *WorkingDirectory*) echo "$FIXTURE_ROOT";;
  *restart*) if [ "${FAIL_RESTART:-0}" = 1 ]; then exit 1; fi; echo restart >> "$FIXTURE_ROOT/restarts";;
  *is-active*) exit 0;;
  *) exit 2;;
esac
''');systemctl.chmod(0o755)
            env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],FIXTURE_ROOT=str(root))
            marker=root/'data'/'.auto-reload-enabled'
            command=['bash',str(scripts/'configure-auto-reload.sh')]
            failed=subprocess.run(command,env=dict(env,FAIL_RESTART='1'),capture_output=True)
            self.assertNotEqual(failed.returncode,0);self.assertFalse(marker.exists())
            subprocess.run(command,env=env,check=True,capture_output=True);self.assertTrue(marker.exists())
            failed=subprocess.run(command+['--disable'],env=dict(env,FAIL_RESTART='1'),capture_output=True)
            self.assertNotEqual(failed.returncode,0);self.assertTrue(marker.exists())
            subprocess.run(command+['--disable'],env=env,check=True,capture_output=True);self.assertFalse(marker.exists())
            self.assertEqual((root/'restarts').read_text().splitlines(),['restart','restart'])
            self.assertFalse((root/'.config').exists())

    def test_denied_service_access_leaves_feature_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'scripts').mkdir();(root/'tools').mkdir()
            shutil.copy(ROOT/'scripts'/'configure-auto-reload.sh',root/'scripts'/'configure-auto-reload.sh')
            tool=root/'tools'/'systemctl';tool.write_text('#!/bin/sh\nexit 1\n');tool.chmod(0o755)
            env=dict(os.environ,PATH=str(root/'tools')+os.pathsep+os.environ['PATH'])
            result=subprocess.run(['bash',str(root/'scripts'/'configure-auto-reload.sh')],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/'data').exists())


if __name__=='__main__':unittest.main()
